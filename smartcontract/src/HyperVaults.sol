// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

/// @notice Test-token vault. Fixed rewards are reserved from sponsor funding, never generated yield.
/// @dev No proxy, sweep, seizure, position transfer or administrator withdrawal exists.
contract HyperVaults is Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant MAX_AMOUNT = type(uint128).max;
    uint256 public constant MAX_OFFERS = 1000;
    uint32 public constant MIN_DURATION = 1 days;
    uint32 public constant MAX_DURATION = 365 days;
    uint16 public constant MAX_APR_BPS = 10_000;
    uint256 public constant REWARD_DENOMINATOR = 10_000 * 365 days;

    struct Offer {
        uint32 duration;
        uint16 aprBps;
        uint256 minDeposit;
        uint256 maxDeposit;
        uint256 capacity;
        uint256 usedCapacity;
        bool open;
    }

    struct Position {
        address owner;
        uint256 principal;
        uint256 reward;
        uint256 offerId;
        uint32 duration;
        uint16 aprBps;
        uint64 depositedAt;
        uint64 maturesAt;
        bool settled;
    }

    IERC20 public immutable token;
    uint256 public offerCount;
    uint256 public nextPositionId = 1;
    uint256 public outstandingPrincipal;
    uint256 public committedRewards;
    bool public depositsPaused;
    mapping(uint256 => Offer) public offers;
    mapping(uint256 => Position) public positions;
    mapping(address => uint256[]) private ownerPositions;

    error TestNetworkOnly();
    error InvalidToken();
    error InvalidTerms();
    error UnknownOffer();
    error TooManyOffers();
    error DepositsPaused();
    error OfferClosed();
    error TermsChanged();
    error InvalidAmount();
    error CapacityExceeded();
    error InsufficientRewardReserve();
    error UnsupportedTokenBehavior();
    error Insolvent();
    error UnknownPosition();
    error NotPositionOwner();
    error NotMature();
    error AlreadySettled();
    error InvalidPage();
    error RenunciationDisabled();

    event OfferCreated(
        uint256 indexed offerId,
        uint32 duration,
        uint16 aprBps,
        uint256 minDeposit,
        uint256 maxDeposit,
        uint256 capacity
    );
    event OfferUpdated(
        uint256 indexed offerId,
        uint32 duration,
        uint16 aprBps,
        uint256 minDeposit,
        uint256 maxDeposit,
        uint256 capacity
    );
    event OfferAvailabilityChanged(uint256 indexed offerId, bool open);
    event DepositPauseChanged(bool paused);
    event RewardsFunded(address indexed sponsor, uint256 amount);
    event PositionOpened(
        uint256 indexed positionId,
        address indexed owner,
        uint256 indexed offerId,
        uint256 principal,
        uint256 reward,
        uint64 depositedAt,
        uint64 maturesAt,
        uint32 duration,
        uint16 aprBps
    );
    event PositionSettled(
        uint256 indexed positionId, address indexed owner, uint256 principal, uint256 reward
    );

    constructor(IERC20 token_, address administrator) Ownable(administrator) {
        if (block.chainid != 31337 && block.chainid != 11155111) revert TestNetworkOnly();
        if (address(token_) == address(0) || address(token_).code.length == 0) {
            revert InvalidToken();
        }
        token = token_;
    }

    function validateTerms(
        uint32 duration,
        uint16 aprBps,
        uint256 minimum,
        uint256 maximum,
        uint256 capacity
    ) internal pure {
        if (
            duration < MIN_DURATION || duration > MAX_DURATION || aprBps > MAX_APR_BPS
                || minimum == 0 || maximum < minimum || capacity < maximum || capacity > MAX_AMOUNT
        ) {
            revert InvalidTerms();
        }
    }

    function createOffer(
        uint32 duration,
        uint16 aprBps,
        uint256 minimum,
        uint256 maximum,
        uint256 capacity
    ) external onlyOwner returns (uint256 id) {
        validateTerms(duration, aprBps, minimum, maximum, capacity);
        if (offerCount == MAX_OFFERS) revert TooManyOffers();
        id = ++offerCount;
        offers[id] = Offer(duration, aprBps, minimum, maximum, capacity, 0, true);
        emit OfferCreated(id, duration, aprBps, minimum, maximum, capacity);
    }

    /// @notice Changes future deposits only. Existing position terms never change.
    function updateOffer(
        uint256 id,
        uint32 duration,
        uint16 aprBps,
        uint256 minimum,
        uint256 maximum,
        uint256 capacity
    ) external onlyOwner {
        validateTerms(duration, aprBps, minimum, maximum, capacity);
        Offer storage offer = offers[id];
        if (id == 0 || id > offerCount) revert UnknownOffer();
        if (capacity < offer.usedCapacity) revert CapacityExceeded();
        offer.duration = duration;
        offer.aprBps = aprBps;
        offer.minDeposit = minimum;
        offer.maxDeposit = maximum;
        offer.capacity = capacity;
        emit OfferUpdated(id, duration, aprBps, minimum, maximum, capacity);
    }

    function setOfferOpen(uint256 id, bool open) external onlyOwner {
        if (id == 0 || id > offerCount) revert UnknownOffer();
        offers[id].open = open;
        emit OfferAvailabilityChanged(id, open);
    }

    function setDepositsPaused(bool paused) external onlyOwner {
        depositsPaused = paused;
        emit DepositPauseChanged(paused);
    }

    function renounceOwnership() public view override onlyOwner {
        revert RenunciationDisabled();
    }

    function liabilities() public view returns (uint256) {
        return outstandingPrincipal + committedRewards;
    }

    function availableRewardReserve() public view returns (uint256) {
        uint256 balance = token.balanceOf(address(this));
        uint256 owed = liabilities();
        return balance >= owed ? balance - owed : 0;
    }

    /// @notice Floors once, in integer base units, using a 365-day simple annual rate.
    function calculateReward(uint256 principal, uint16 aprBps, uint32 duration)
        public
        pure
        returns (uint256)
    {
        if (
            principal > MAX_AMOUNT || aprBps > MAX_APR_BPS || duration < MIN_DURATION
                || duration > MAX_DURATION
        ) revert InvalidTerms();
        return Math.mulDiv(principal, uint256(aprBps) * duration, REWARD_DENOMINATOR);
    }

    /// @notice Any sponsor may prefund. Funding cannot be withdrawn by an administrator.
    function fundRewards(uint256 amount) external nonReentrant {
        if (amount == 0 || amount > MAX_AMOUNT) revert InvalidAmount();
        uint256 beforeBalance = token.balanceOf(address(this));
        emit RewardsFunded(msg.sender, amount);
        token.safeTransferFrom(msg.sender, address(this), amount);
        // Exact balance deltas deliberately reject transfer-tax/rebasing behavior.
        // forge-lint: disable-next-line(incorrect-strict-equality)
        if (token.balanceOf(address(this)) != beforeBalance + amount) {
            revert UnsupportedTokenBehavior();
        }
    }

    /// @notice Expected duration/rate prevent an offer change from silently changing a reviewed lock.
    function deposit(
        uint256 offerId,
        uint256 amount,
        uint32 expectedDuration,
        uint16 expectedAprBps
    ) external nonReentrant returns (uint256 id) {
        if (depositsPaused) revert DepositsPaused();
        if (offerId == 0 || offerId > offerCount) revert UnknownOffer();
        Offer storage offer = offers[offerId];
        if (!offer.open) revert OfferClosed();
        if (offer.duration != expectedDuration || offer.aprBps != expectedAprBps) {
            revert TermsChanged();
        }
        if (amount < offer.minDeposit || amount > offer.maxDeposit) revert InvalidAmount();
        if (amount > offer.capacity - offer.usedCapacity) revert CapacityExceeded();
        uint256 reward = calculateReward(amount, offer.aprBps, offer.duration);
        uint256 beforeBalance = token.balanceOf(address(this));
        if (beforeBalance < liabilities()) revert Insolvent();
        if (beforeBalance - liabilities() < reward) revert InsufficientRewardReserve();
        if (block.timestamp > type(uint64).max - offer.duration) revert InvalidTerms();

        id = nextPositionId++;
        // Safe because the preceding bound includes the entire lock duration.
        // forge-lint: disable-next-line(unsafe-typecast)
        uint64 depositedAt = uint64(block.timestamp);
        uint64 maturesAt = depositedAt + offer.duration;
        positions[id] = Position(
            msg.sender,
            amount,
            reward,
            offerId,
            offer.duration,
            offer.aprBps,
            depositedAt,
            maturesAt,
            false
        );
        ownerPositions[msg.sender].push(id);
        outstandingPrincipal += amount;
        committedRewards += reward;
        offer.usedCapacity += amount;
        emit PositionOpened(
            id,
            msg.sender,
            offerId,
            amount,
            reward,
            depositedAt,
            maturesAt,
            offer.duration,
            offer.aprBps
        );
        token.safeTransferFrom(msg.sender, address(this), amount);
        // forge-lint: disable-next-line(incorrect-strict-equality)
        if (token.balanceOf(address(this)) != beforeBalance + amount) {
            revert UnsupportedTokenBehavior();
        }
    }

    /// @notice One owner, one position, constant work. Deposit pause never blocks settlement.
    function withdraw(uint256 id) external nonReentrant {
        Position storage position = positions[id];
        if (position.owner == address(0)) revert UnknownPosition();
        if (position.owner != msg.sender) revert NotPositionOwner();
        if (position.settled) revert AlreadySettled();
        if (block.timestamp < position.maturesAt) revert NotMature();
        uint256 payout = position.principal + position.reward;
        position.settled = true;
        outstandingPrincipal -= position.principal;
        committedRewards -= position.reward;
        offers[position.offerId].usedCapacity -= position.principal;
        uint256 beforeOwnerBalance = token.balanceOf(msg.sender);
        emit PositionSettled(id, msg.sender, position.principal, position.reward);
        token.safeTransfer(msg.sender, payout);
        // forge-lint: disable-next-line(incorrect-strict-equality)
        if (token.balanceOf(msg.sender) != beforeOwnerBalance + payout) {
            revert UnsupportedTokenBehavior();
        }
    }

    function ownerPositionCount(address account) external view returns (uint256) {
        return ownerPositions[account].length;
    }

    /// @notice Bounded pagination for direct reads, independent of the website's index.
    function ownerPositionIds(address account, uint256 offset, uint256 limit)
        external
        view
        returns (uint256[] memory result)
    {
        if (limit == 0 || limit > 100) revert InvalidPage();
        uint256 count = ownerPositions[account].length;
        if (offset >= count) return new uint256[](0);
        uint256 size = Math.min(limit, count - offset);
        result = new uint256[](size);
        for (uint256 i = 0; i < size; ++i) {
            result[i] = ownerPositions[account][offset + i];
        }
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {HyperVaults} from "../src/HyperVaults.sol";
import {TestToken} from "../src/TestToken.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

interface Vm {
    function warp(uint256 timestamp) external;
    function chainId(uint256 chainId) external;
    function prank(address sender) external;
    function startPrank(address sender) external;
    function stopPrank() external;
    function expectRevert(bytes4 selector) external;
    function expectRevert() external;
}

/// @dev A deliberately unsupported token proves that fee-bearing transfers roll back.
contract FeeToken is ERC20 {
    constructor() ERC20("Fee fixture", "FEE") {
        _mint(msg.sender, 1_000_000 ether);
    }

    function _update(address from, address to, uint256 value) internal override {
        if (from != address(0) && to != address(0) && value > 1) {
            super._update(from, address(0), 1);
            value -= 1;
        }
        super._update(from, to, value);
    }
}

contract HyperVaultsTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    TestToken private token;
    HyperVaults private vault;
    address private constant MEMBER = address(0xA11CE);
    address private constant OTHER = address(0xB0B);

    function setUp() public {
        vm.chainId(31337);
        vm.warp(1000);
        token = new TestToken(address(this));
        vault = new HyperVaults(token, address(this));
        vault.createOffer(1 days, 1000, 1 ether, 1000 ether, 10_000 ether);
        token.approve(address(vault), 10_000 ether);
        vault.fundRewards(10_000 ether);
        token.transfer(MEMBER, 1000 ether);
        vm.prank(MEMBER);
        token.approve(address(vault), 1000 ether);
    }

    function deposit() private returns (uint256) {
        vm.prank(MEMBER);
        return vault.deposit(1, 100 ether, 1 days, 1000);
    }

    function testDepositReservesFullRewardAndPaysAtMaturity() public {
        uint256 id = deposit();
        uint256 reward = vault.calculateReward(100 ether, 1000, 1 days);
        require(vault.outstandingPrincipal() == 100 ether);
        require(vault.committedRewards() == reward);
        require(token.balanceOf(address(vault)) >= vault.liabilities());
        vm.warp(1000 + 1 days);
        vm.prank(MEMBER);
        vault.withdraw(id);
        require(token.balanceOf(MEMBER) == 1000 ether + reward);
        require(vault.liabilities() == 0);
    }

    function testEarlyWithdrawalIsRefused() public {
        uint256 id = deposit();
        vm.expectRevert(HyperVaults.NotMature.selector);
        vm.prank(MEMBER);
        vault.withdraw(id);
    }

    function testAnotherWalletCannotWithdraw() public {
        uint256 id = deposit();
        vm.warp(1000 + 1 days);
        vm.expectRevert(HyperVaults.NotPositionOwner.selector);
        vm.prank(OTHER);
        vault.withdraw(id);
    }

    function testWithdrawalCannotBeRepeated() public {
        uint256 id = deposit();
        vm.warp(1000 + 1 days);
        vm.startPrank(MEMBER);
        vault.withdraw(id);
        vm.expectRevert(HyperVaults.AlreadySettled.selector);
        vault.withdraw(id);
        vm.stopPrank();
    }

    function testPauseBlocksDepositsButAllowsMatureWithdrawal() public {
        uint256 id = deposit();
        vault.setDepositsPaused(true);
        vm.expectRevert(HyperVaults.DepositsPaused.selector);
        vm.prank(MEMBER);
        vault.deposit(1, 1 ether, 1 days, 1000);
        vm.warp(1000 + 1 days);
        vm.prank(MEMBER);
        vault.withdraw(id);
        require(vault.liabilities() == 0);
    }

    function testOfferUpdatesCannotAlterExistingPosition() public {
        uint256 id = deposit();
        uint256 reserved = vault.committedRewards();
        vault.updateOffer(1, 30 days, 2000, 1 ether, 1000 ether, 10_000 ether);
        vm.expectRevert(HyperVaults.TermsChanged.selector);
        vm.prank(MEMBER);
        vault.deposit(1, 1 ether, 1 days, 1000);
        vm.warp(1000 + 1 days);
        vm.prank(MEMBER);
        vault.withdraw(id);
        require(token.balanceOf(MEMBER) == 1000 ether + reserved);
    }

    function testOnlyOwnerCanChangeOffersOrPause() public {
        vm.startPrank(OTHER);
        vm.expectRevert();
        vault.setDepositsPaused(true);
        vm.expectRevert();
        vault.createOffer(1 days, 100, 1 ether, 10 ether, 100 ether);
        vm.expectRevert();
        vault.setOfferOpen(1, false);
        vm.stopPrank();
    }

    function testOwnershipNeedsExplicitAcceptanceAndCannotBeRenounced() public {
        vault.transferOwnership(OTHER);
        require(vault.owner() == address(this) && vault.pendingOwner() == OTHER);
        vm.prank(OTHER);
        vault.acceptOwnership();
        require(vault.owner() == OTHER);
        vm.expectRevert(HyperVaults.RenunciationDisabled.selector);
        vm.prank(OTHER);
        vault.renounceOwnership();
    }

    function testUnfundedRewardCannotBePromised() public {
        HyperVaults empty = new HyperVaults(token, address(this));
        empty.createOffer(1 days, 1000, 1 ether, 1000 ether, 10_000 ether);
        vm.startPrank(MEMBER);
        token.approve(address(empty), 100 ether);
        vm.expectRevert(HyperVaults.InsufficientRewardReserve.selector);
        empty.deposit(1, 100 ether, 1 days, 1000);
        vm.stopPrank();
        require(empty.liabilities() == 0);
    }

    function testClosedOfferAndInvalidAmountAreRefused() public {
        vault.setOfferOpen(1, false);
        vm.expectRevert(HyperVaults.OfferClosed.selector);
        vm.prank(MEMBER);
        vault.deposit(1, 1 ether, 1 days, 1000);
        vault.setOfferOpen(1, true);
        vm.expectRevert(HyperVaults.InvalidAmount.selector);
        vm.prank(MEMBER);
        vault.deposit(1, 0, 1 days, 1000);
    }

    function testCapacityCannotDropBelowExistingDeposits() public {
        deposit();
        vm.expectRevert(HyperVaults.CapacityExceeded.selector);
        vault.updateOffer(1, 1 days, 1000, 1 ether, 10 ether, 50 ether);
    }

    function testFeeTokenFundingIsRejectedAtomically() public {
        FeeToken fee = new FeeToken();
        HyperVaults fixture = new HyperVaults(fee, address(this));
        fee.approve(address(fixture), 100 ether);
        vm.expectRevert(HyperVaults.UnsupportedTokenBehavior.selector);
        fixture.fundRewards(100 ether);
        require(fee.balanceOf(address(fixture)) == 0);
    }

    function testFaucetHasPerWalletCooldown() public {
        vm.startPrank(OTHER);
        token.faucet();
        vm.expectRevert(TestToken.FaucetCoolingDown.selector);
        token.faucet();
        vm.warp(1000 + 1 hours);
        token.faucet();
        vm.stopPrank();
        require(token.balanceOf(OTHER) == 2000 ether);
    }

    function testMainnetAndInvalidTokenAreRejected() public {
        vm.expectRevert(HyperVaults.InvalidToken.selector);
        new HyperVaults(IERC20(address(0)), address(this));
        vm.chainId(1);
        vm.expectRevert(TestToken.TestNetworkOnly.selector);
        new TestToken(address(this));
        vm.expectRevert(HyperVaults.TestNetworkOnly.selector);
        new HyperVaults(token, address(this));
    }

    function testPaginationIsBoundedAndOwnerScoped() public {
        uint256 id = deposit();
        uint256[] memory own = vault.ownerPositionIds(MEMBER, 0, 100);
        require(own.length == 1 && own[0] == id);
        require(vault.ownerPositionIds(OTHER, 0, 100).length == 0);
        vm.expectRevert(HyperVaults.InvalidPage.selector);
        vault.ownerPositionIds(MEMBER, 0, 101);
    }

    function testFuzzRewardUsesIntegerFloor(uint128 principal, uint16 rate, uint32 duration)
        public
        view
    {
        uint16 boundedRate = uint16(uint256(rate) % 10_001);
        uint32 boundedDuration = uint32(1 days + uint256(duration) % (364 days + 1));
        uint256 expected = uint256(principal) * boundedRate * boundedDuration / (10_000 * 365 days);
        require(vault.calculateReward(principal, boundedRate, boundedDuration) == expected);
    }
}

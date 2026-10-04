// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice Unpriced demonstration token, restricted to Anvil and Sepolia. Never a real asset.
contract TestToken is ERC20 {
    uint256 public constant FAUCET_AMOUNT = 1000 ether;
    uint256 public constant FAUCET_COOLDOWN = 1 hours;
    mapping(address => uint256) public lastFaucet;
    error TestNetworkOnly();
    error FaucetCoolingDown();
    event FaucetClaimed(address indexed account, uint256 amount);

    constructor(address sponsor) ERC20("HyperVaults TEST TOKEN", "HVT") {
        if (block.chainid != 31337 && block.chainid != 11155111) revert TestNetworkOnly();
        _mint(sponsor, 1_000_000 ether);
    }

    function faucet() external {
        uint256 previous = lastFaucet[msg.sender];
        if (previous != 0 && block.timestamp < previous + FAUCET_COOLDOWN) {
            revert FaucetCoolingDown();
        }
        lastFaucet[msg.sender] = block.timestamp;
        _mint(msg.sender, FAUCET_AMOUNT);
        emit FaucetClaimed(msg.sender, FAUCET_AMOUNT);
    }
}

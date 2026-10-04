import {BrowserProvider, Contract, formatUnits, parseUnits, keccak256} from '/static/vendor/ethers-6.17.0.min.js';
import {api, element, icon} from './api.js?v=2';

export function amount(text) {
  if (!/^(0|[1-9][0-9]*)(\.[0-9]{1,18})?$/.test(text.trim())) throw new Error('Enter a positive token amount with at most 18 decimal places.');
  const value = parseUnits(text.trim(), 18);
  if (value <= 0n || value > (1n << 128n) - 1n) throw new Error('Amount is outside the supported bounds.');
  return value;
}

export function tokens(value, exact = false) {
  const [whole, fraction = ''] = formatUnits(BigInt(value), 18).split('.');
  const digits = exact ? fraction : fraction.slice(0, 4);
  const trimmed = digits.replace(/0+$/, '');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return trimmed ? `${grouped}.${trimmed}` : grouped;
}
export function rate(bps) { return `${Math.trunc(bps / 100)}.${String(bps % 100).padStart(2, '0')}%`; }

function rpcErrorCode(error) {
  const codes = [error?.code, error?.data?.originalError?.code, error?.error?.code, error?.info?.error?.code].map(Number);
  return codes.find(code => [4001, 4902, -32002, 4200, -32601].includes(code)) ?? codes[0];
}

export function walletError(error) {
  if (rpcErrorCode(error) === -32002 || error.code === 'WALLET_REQUEST_PENDING') return 'A wallet request is already open. Complete or cancel it in your wallet, then try again.';
  if (rpcErrorCode(error) === 4001 || error.code === 'ACTION_REJECTED') return 'Transaction or signature rejected in the wallet. Nothing further was sent.';
  if (error.code === 'INSUFFICIENT_FUNDS') return 'Insufficient test ETH for gas. Fund local gas using the startup helper.';
  if (error.code === 'CALL_EXCEPTION') return 'The contract rejected this transaction. Refresh the terms, funding and maturity before retrying.';
  if (error.code === 'TIMEOUT') return 'Transaction is still pending. Its hash has been retained in this tab for verification.';
  if (error.code === 'TRANSACTION_REPLACED') return 'The wallet replaced or cancelled this transaction. Refresh and inspect its receipt before retrying.';
  return String(error.shortMessage || error.message || 'Wallet operation failed.').slice(0, 350);
}

const providers = new Map();
const brands = {
  'io.metamask': {name: 'MetaMask', logo: 'metamask', url: 'https://metamask.io/download/'},
  'io.rabby': {name: 'Rabby', logo: 'rabby', url: 'https://rabby.io/'},
};
window.addEventListener('eip6963:announceProvider', event => {
  const {info, provider} = event.detail || {};
  if (!info || !provider || typeof provider.request !== 'function' || typeof info.uuid !== 'string' || typeof info.name !== 'string' || typeof info.rdns !== 'string') return;
  if (providers.size >= 16 || !/^[a-z0-9.-]{1,120}$/i.test(info.rdns)) return;
  // Metadata is self-reported. Never inject a wallet's SVG, HTML or remote icon.
  if (!providers.has(info.uuid)) providers.set(info.uuid, {id: info.rdns, name: info.name.slice(0, 50), provider});
});
window.dispatchEvent(new Event('eip6963:requestProvider'));

function availableWallets() {
  const found = [...providers.values()];
  const legacy = window.ethereum?.providers || (window.ethereum ? [window.ethereum] : []);
  for (const provider of legacy) {
    if (!provider || typeof provider.request !== 'function' || found.some(wallet => wallet.provider === provider)) continue;
    const id = provider.isRabby ? 'io.rabby' : provider.isMetaMask ? 'io.metamask' : provider.isCoinbaseWallet ? 'com.coinbase.wallet' : 'browser';
    // EIP-6963 announcements take priority over the legacy shared provider.
    if (found.some(wallet => wallet.id === id)) continue;
    found.push({id, name: brands[id]?.name || (id === 'com.coinbase.wallet' ? 'Coinbase Wallet' : 'Browser wallet'), provider});
  }
  return found;
}

function selectWallet() {
  const dialog = element('dialog', 'wallet-picker'); dialog.id = 'wallet-picker';
  dialog.setAttribute('aria-labelledby', 'wallet-picker-title');
  const close = element('button', 'icon-button dialog-close'); close.type = 'button';
  close.setAttribute('aria-label', 'Close wallet picker'); close.append(icon('close'));
  const title = element('h2', '', 'Choose your wallet'); title.id = 'wallet-picker-title';
  const options = element('div', 'wallet-options');
  dialog.append(close, title, element('p', 'muted', 'Connect a browser wallet to continue.'), options, element('p', 'wallet-picker-note', 'Use a test wallet. Approve requests in your wallet.'));
  document.body.append(dialog);
  return new Promise((resolve, reject) => {
    let selected = null;
    function render() {
      options.replaceChildren();
      const found = availableWallets();
      const entries = [...found, ...Object.entries(brands).filter(([id]) => !found.some(wallet => wallet.id === id)).map(([id, brand]) => ({id, ...brand}))];
      for (const entry of entries) {
        const brand = brands[entry.id], row = element(entry.provider ? 'button' : 'a', 'wallet-option');
        if (brand) { const logo = element('img'); logo.src = `/static/wallets/${brand.logo}.svg`; logo.alt = ''; row.append(logo); }
        else row.append(icon('wallet'));
        const text = element('span'); text.append(element('strong', '', entry.name), element('small', '', entry.provider ? 'Detected in this browser' : 'Get browser extension'));
        const arrow = icon(entry.provider ? 'arrow-right' : 'arrow-up-right'); arrow.classList.add('option-arrow'); row.append(text, arrow);
        if (entry.provider) {
          row.type = 'button'; row.addEventListener('click', () => { selected = entry; dialog.close(); });
        } else { row.href = entry.url; row.target = '_blank'; row.rel = 'noopener noreferrer'; row.setAttribute('aria-label', `Get ${entry.name} browser extension`); }
        options.append(row);
      }
    }
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
      window.removeEventListener('eip6963:announceProvider', render); dialog.remove();
      if (selected) resolve(selected);
      else { const error = new Error('Wallet selection cancelled.'); error.code = 'WALLET_SELECTION_CANCELLED'; reject(error); }
    }, {once: true});
    window.addEventListener('eip6963:announceProvider', render);
    render(); dialog.showModal(); window.dispatchEvent(new Event('eip6963:requestProvider'));
  });
}

export class Wallet extends EventTarget {
  constructor(config) {
    super(); this.config = config; this.address = null; this.generation = 0; this.provider = null; this.injected = null; this.selected = null;
    this.bound = new WeakSet(); this.connecting = false; this.accountGeneration = 0;
  }
  bind(entry) {
    this.injected = entry.provider; this.selected = entry;
    if (this.bound.has(entry.provider) || !entry.provider.on) return;
    this.bound.add(entry.provider);
    for (const [event, reason] of [
      ['accountsChanged', 'Wallet account changed. Reconnect explicitly.'],
      ['chainChanged', 'Wallet network changed. Reconnect explicitly.'],
      ['disconnect', 'Wallet disconnected. Reconnect to continue.'],
    ]) entry.provider.on(event, () => {
      if (this.injected !== entry.provider) return;
      if (event !== 'chainChanged') this.accountGeneration++;
      this.invalidate(reason);
    });
  }
  invalidate(reason) {
    this.address = null; this.provider = null; this.generation++;
    sessionStorage.removeItem('hv_wallet_connection');
    this.dispatchEvent(new CustomEvent('change', {detail: reason}));
  }
  status(message) { this.dispatchEvent(new CustomEvent('status', {detail: message})); }
  async ensureNetwork(entry) {
    const target = BigInt(this.config.chain_id), chainId = `0x${target.toString(16)}`;
    if (BigInt(await entry.provider.request({method: 'eth_chainId'})) === target) return;
    const label = `${this.config.network} (chain ${this.config.chain_id})`;
    const switchChain = () => {
      this.status(`Approve switching to ${label} in ${entry.name}.`);
      return entry.provider.request({method: 'wallet_switchEthereumChain', params: [{chainId}]});
    };
    try {
      try { await switchChain(); }
      catch (error) {
        if (rpcErrorCode(error) !== 4902 || target !== 31337n) throw error;
        this.status(`Approve adding ${label} in ${entry.name}.`);
        await entry.provider.request({method: 'wallet_addEthereumChain', params: [{chainId, chainName: 'Anvil local', nativeCurrency: {name: 'Test ETH', symbol: 'ETH', decimals: 18}, rpcUrls: ['http://127.0.0.1:8545']}]});
        // Adding a chain does not guarantee it became the active chain.
        if (BigInt(await entry.provider.request({method: 'eth_chainId'})) !== target) await switchChain();
      }
    } catch (error) {
      const code = rpcErrorCode(error);
      const details = target === 31337n ? `${label}, RPC http://127.0.0.1:8545` : label;
      let message;
      if (code === 4001) message = `Network change cancelled. Retry and approve ${label} in your wallet.`;
      else if (code === -32002) message = 'A wallet request is already open. Complete or cancel it in your wallet, then try again.';
      else if ([4200, -32601, 4902].includes(code)) message = `Select ${details} in your wallet, then try again. This wallet could not switch automatically.`;
      else message = `Could not switch to ${label}. Check your wallet's network settings${target === 31337n ? ' and that the local node is running at http://127.0.0.1:8545' : ''}, then try again.`;
      const failure = new Error(message); failure.code = 'WALLET_NETWORK_FAILED'; throw failure;
    }
    if (BigInt(await entry.provider.request({method: 'eth_chainId'})) !== target) throw new Error(`Your wallet is still on another network. Select ${label} in your wallet, then try again.`);
  }
  async connect(entry = null) {
    if (this.connecting) { const error = new Error('Wallet connection pending.'); error.code = 'WALLET_REQUEST_PENDING'; throw error; }
    this.connecting = true;
    try {
      entry ||= await selectWallet();
      this.invalidate('Connecting wallet…'); this.bind(entry);
      this.status(`Approve the connection in ${entry.name}.`);
      const accounts = await entry.provider.request({method: 'eth_requestAccounts'});
      if (!accounts.length) throw new Error('No wallet account was selected.');
      const address = accounts[0].toLowerCase(), accountGeneration = this.accountGeneration;
      await this.ensureNetwork(entry);
      const generation = this.generation;
      const current = await entry.provider.request({method: 'eth_accounts'});
      const chain = BigInt(await entry.provider.request({method: 'eth_chainId'}));
      if (this.injected !== entry.provider || generation !== this.generation || accountGeneration !== this.accountGeneration || !current.length || current[0].toLowerCase() !== address || chain !== BigInt(this.config.chain_id)) {
        throw new Error('Wallet account or network changed during connection. Try again with your selected account.');
      }
      // Build a fresh provider after switching; never reuse a previous chain context.
      this.provider = new BrowserProvider(entry.provider, 'any');
      this.address = address; this.generation++;
      sessionStorage.setItem('hv_wallet_connection', JSON.stringify({address: this.address, chain: this.config.chain_id, genesis: this.config.genesis_hash, id: entry.id, name: entry.name}));
      this.dispatchEvent(new CustomEvent('change', {detail: ''}));
      return this.address;
    } catch (error) {
      if (rpcErrorCode(error) === 4001) {
        const failure = new Error('Wallet connection cancelled. Retry and approve the connection in your wallet.');
        failure.code = 'WALLET_CONNECTION_CANCELLED'; throw failure;
      }
      throw error;
    } finally { this.connecting = false; this.status(''); }
  }
  async resume() {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem('hv_wallet_connection') || 'null'); } catch { return false; }
    if (!saved || saved.chain !== this.config.chain_id || saved.genesis !== this.config.genesis_hash) return false;
    const matches = availableWallets().filter(entry => entry.id === saved.id && entry.name === saved.name);
    if (matches.length !== 1) return false;
    const entry = matches[0]; this.bind(entry);
    const generation = this.generation;
    const accounts = await entry.provider.request({method: 'eth_accounts'});
    const chain = BigInt(await entry.provider.request({method: 'eth_chainId'}));
    if (generation !== this.generation) return false;
    if (!accounts.length || accounts[0].toLowerCase() !== saved.address || chain !== BigInt(saved.chain)) {
      this.invalidate('Previously selected wallet changed. Reconnect explicitly.'); return false;
    }
    this.address = saved.address; this.provider = new BrowserProvider(entry.provider, 'any'); this.generation++;
    this.dispatchEvent(new CustomEvent('change', {detail: ''}));
    return true;
  }
  async switchNetwork() {
    return this.connect(this.selected);
  }
  async assertContext(generation, address) {
    if (!this.address || this.generation !== generation || this.address !== address) throw new Error('Wallet context changed. No further transaction will be sent. Reconnect explicitly.');
    const accounts = await this.injected.request({method: 'eth_accounts'});
    const chainId = BigInt(await this.injected.request({method: 'eth_chainId'}));
    if (!this.address || this.generation !== generation || !accounts.length || accounts[0].toLowerCase() !== address || chainId !== BigInt(this.config.chain_id)) {
      this.invalidate('Wallet address or network changed.');
      throw new Error('Wallet address or network changed. Reconnect explicitly.');
    }
  }
  async context(user, owner = null) {
    if (!this.address) throw new Error('Connect your wallet first. Connecting and website authentication are separate actions.');
    const address = this.address, generation = this.generation;
    await this.assertContext(generation, address);
    if (!user.wallets.includes(address)) throw new Error('This wallet is not linked to your website account. Link it in Security with a signed ownership proof.');
    if (owner && owner.toLowerCase() !== address) throw new Error('Connect the wallet authorized for this position or contract action.');
    const [genesis, vaultCode, tokenCode] = await Promise.all([
      this.provider.getBlock(0), this.provider.getCode(this.config.vault_address), this.provider.getCode(this.config.token_address),
    ]);
    if (genesis.hash !== this.config.genesis_hash || keccak256(vaultCode) !== this.config.vault_code_hash || keccak256(tokenCode) !== this.config.token_code_hash) throw new Error('Wallet RPC chain or contract code does not match the verified deployment. No transaction was sent.');
    await this.assertContext(generation, address);
    const signer = await this.provider.getSigner(address);
    return {address, generation, signer,
      token: new Contract(this.config.token_address, this.config.token_abi, signer),
      vault: new Contract(this.config.vault_address, this.config.vault_abi, signer)};
  }
  async authenticate(action) {
    if (!this.address) await this.connect();
    const address = this.address, generation = this.generation;
    const challenge = await api('/auth/challenge', {method: 'POST', body: {address, action}});
    await this.assertContext(generation, address);
    const signer = await this.provider.getSigner(address);
    const signature = await signer.signMessage(challenge.message);
    await this.assertContext(generation, address);
    return api('/auth/verify', {method: 'POST', body: {id: challenge.id, message: challenge.message, signature}});
  }
}

import {Wallet, walletError} from '/static/js/wallet.js';

const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="setup-token"]').content;
const wallet = new Wallet({chain_id: 11155111, network: 'Sepolia', genesis_hash: '0x25a5cc106eea7138acab33231d7160d69cb777ee0c2c553fcddf5138993e6dd9'});
let current = null, busy = false, timer = null;
let reverted = false;
const journalKey = 'hv_sepolia_pending';

async function request(path, body) {
  const response = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'X-Setup-Token': token, ...(body ? {'Content-Type': 'application/json'} : {})}, body: body ? JSON.stringify(body) : undefined});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Setup could not continue.');
  return result;
}
function showError(error) {
  $('status').textContent = walletError(error); $('status').classList.add('is-error'); $('retry').hidden = false;
}
async function refresh() {
  clearTimeout(timer); $('continue').disabled = true;
  try {
    // Save the hash before waiting for a receipt; never resend after a timeout/reload.
    const pending = JSON.parse(localStorage.getItem(journalKey) || 'null');
    if (pending) {
      const result = await request('/transaction', pending);
      reverted = result.reverted; localStorage.removeItem(journalKey);
    }
    current = await request('/status');
    $('status').classList.remove('is-error'); $('retry').hidden = true;
    $('steps').replaceChildren(...current.steps.map(step => {
      const item = document.createElement('li'), link = document.createElement('a');
      link.textContent = `${step.label} ✓`; link.className = 'text-link';
      link.href = `https://sepolia.etherscan.io/tx/${step.hash}`; link.target = '_blank'; link.rel = 'noopener noreferrer'; item.append(link); return item;
    }));
    if (current.failed) {
      $('owner').textContent = current.owner;
      $('status').textContent = `${current.label} failed · ${Math.min(current.confirmations, 6)}/6 confirmations`;
      $('cost').textContent = 'Retry prepares a new wallet request.';
      $('continue').textContent = `Retry ${current.label.toLowerCase()}`;
      $('continue').disabled = busy || !current.can_retry;
      if (!current.can_retry) timer = setTimeout(refresh, 12000);
      return;
    }
    if (current.pending) {
      $('status').textContent = `${current.label} · ${Math.min(current.confirmations, 6)}/6 confirmations`;
      $('cost').textContent = ''; timer = setTimeout(refresh, 12000); return;
    }
    $('owner').textContent = current.owner;
    $('status').textContent = current.ready ? 'Ready to activate' : `Step ${current.index + 1} of 7 · ${current.label}`;
    $('cost').textContent = reverted ? 'Previous transaction reverted. Use standard wallet transactions to retry.' : current.ready ? '' : 'Review fees in your wallet.';
    $('continue').textContent = current.ready ? 'Activate Sepolia' : wallet.address ? current.label : 'Connect wallet';
    $('continue').disabled = busy;
  } catch (error) { showError(error); }
}
wallet.addEventListener('status', event => { if (event.detail) $('status').textContent = event.detail; });
$('retry').addEventListener('click', refresh);
$('continue').addEventListener('click', async () => {
  if (busy) return;
  let failure = null;
  busy = true; $('continue').disabled = true; $('continue').setAttribute('aria-busy', 'true');
  try {
    if (current.failed) {
      await request('/retry-failed', {index: current.index, hash: current.failed});
      reverted = false; return;
    }
    if (current.ready) {
      await request('/activate', {});
      $('status').textContent = 'Sepolia activated. Starting HyperVaults…';
      $('open-app').hidden = false; $('continue').hidden = true; return;
    }
    if (!wallet.address) {
      await wallet.connect();
      if (wallet.address !== current.owner.toLowerCase()) { wallet.invalidate(''); throw new Error('Select the contract-owner address shown above in your wallet.'); }
      return;
    }
    if (wallet.address !== current.owner.toLowerCase()) { wallet.invalidate(''); throw new Error('Select the contract-owner address shown above in your wallet.'); }
    await wallet.assertContext(wallet.generation, wallet.address);
    const hash = await wallet.injected.request({method: 'eth_sendTransaction', params: [current.transaction]});
    localStorage.setItem(journalKey, JSON.stringify({index: current.index, hash}));
    const result = await request('/transaction', {index: current.index, hash});
    reverted = result.reverted; localStorage.removeItem(journalKey);
  } catch (error) { failure = error; }
  finally {
    busy = false; $('continue').setAttribute('aria-busy', 'false');
    if (!$('continue').hidden) await refresh();
    if (failure) showError(failure);
  }
});
await refresh();

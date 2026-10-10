import {api, boot, $, element, empty, emptyState, icon, disclosure, button, detail, shortAddress, dateTime, dateString} from './api.js?v=2';
import {Wallet, amount, tokens, rate, walletError} from './wallet.js?v=5';

const view = location.pathname.slice(1) || 'overview';
const screen = {
  overview: ['Overview', 'Your tokens at a glance.'],
  vaults: ['Vaults', 'Choose your lock. Know your reward.'],
  positions: ['Positions', 'Track your locks and withdraw at maturity.'],
  security: ['Security', 'Your wallets, passwords and sessions.'],
  admin: ['Administration', 'Manage funding, offers and accounts.'],
}[view] || ['Overview', 'Your positions and rewards.'];
let user = null, config = null, wallet = null, snapshot = null, prepared = null, transactionBusy = false;
let toastTimer = null, refreshing = false, notePosition = null, positionFilter = 'all', previewFocus = null;
let linkingWallet = false, passwordBusy = null;

document.title = `HyperVaults · ${screen[0]}`;
$('page-title').textContent = $('breadcrumb').textContent = screen[0];
$('page-description').textContent = screen[1];
for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== `view-${view}`;
document.querySelector(`[data-nav="${view}"]`)?.setAttribute('aria-current', 'page');

function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 7000);
}
function report(error) {
  if (error.code === 'WALLET_SELECTION_CANCELLED') return;
  if (error.status === 401) { location.assign('/login'); return; }
  $('error-banner').textContent = walletError(error); $('error-banner').hidden = false;
}
function action(callback) {
  return async event => {
    const target = event?.currentTarget;
    const controls = target?.tagName === 'FORM' ? [...target.querySelectorAll('button[type="submit"]')] : target ? [target] : [];
    if (controls.some(control => control.disabled)) return;
    for (const control of controls) control.disabled = true;
    try { await callback(event); }
    catch (error) { report(error); }
    finally { for (const control of controls) control.disabled = false; }
  };
}
function account() {
  $('account-name').textContent = user.name;
  $('avatar').textContent = user.name.slice(0, 1).toUpperCase();
  $('account-role').textContent = user.role === 'admin' ? 'Administrator' : 'Member';
  $('admin-nav').hidden = user.role !== 'admin';
  walletSetupState();
}
function passwordFormState() {
  if (!user) return;
  const setup = !user.has_password, form = $('password-form');
  $('password-title').textContent = setup ? 'Set up email & password' : 'Email & password';
  $('password-email').hidden = setup; $('password-email').textContent = user.email || '';
  $('password-email-label').hidden = !setup; form.elements.email.required = setup;
  $('current-password-label').hidden = setup; form.elements.current_password.required = !setup;
  $('new-password-label').textContent = setup ? 'Password' : 'New password';
  $('password-setup-hint').textContent = setup ? '12+ characters. Confirm setup with your linked wallet.' : '12+ characters. Other sessions will sign out.';
  $('save-password').textContent = passwordBusy === 'wallet' ? 'Waiting for wallet…' : passwordBusy ? 'Saving…' : setup ? 'Save email & password' : 'Update password';
  $('save-password').disabled = !!passwordBusy || linkingWallet || (setup && (!wallet?.address || !user.wallets.includes(wallet.address)));
  $('save-password').setAttribute('aria-busy', String(!!passwordBusy));
  $('reauth').hidden = setup; $('reauth').disabled = !!passwordBusy;
}
function walletSetupState() {
  if (!user) return;
  const connected = !!wallet?.address, linked = connected && user.wallets.includes(wallet.address);
  $('wallet-setup').hidden = user.wallets.length > 0 || view === 'admin';
  $('wallet-setup-title').textContent = connected ? 'Link your wallet' : 'Connect your wallet';
  $('wallet-setup-description').textContent = connected ? `${shortAddress(wallet.address)} · Sign an ownership proof to link it to this account.` : "You're signed in. Connect, then sign to link your wallet.";
  for (const [id, current, complete] of [['setup-connect', !connected, connected], ['setup-link', connected, false]]) {
    $(id).classList.toggle('is-complete', complete);
    if (current) $(id).setAttribute('aria-current', 'step'); else $(id).removeAttribute('aria-current');
  }
  for (const id of ['wallet-setup-action', 'link-wallet']) {
    $(id).textContent = linkingWallet ? 'Waiting for signature…' : wallet?.connecting ? 'Connecting…' : linked ? 'Wallet linked' : connected ? 'Link wallet' : 'Connect wallet';
    $(id).disabled = !wallet || wallet.connecting || linkingWallet || linked || !!passwordBusy;
    $(id).setAttribute('aria-busy', String(linkingWallet || !!wallet?.connecting));
  }
  $('wallet-reauth').disabled = !linked || linkingWallet || wallet?.connecting || !!passwordBusy;
  $('connect-wallet').disabled = !wallet || wallet.connecting || linkingWallet || !!passwordBusy;
  passwordFormState();
}
function walletState(message = '') {
  if (!wallet) return;
  walletSetupState();
  if (!$('wallet-setup').hidden && message) $('wallet-setup-description').textContent = message;
  $('wallet-button-label').textContent = wallet.address ? shortAddress(wallet.address) : 'Connect wallet';
  $('connect-wallet').title = wallet.address ? `${wallet.selected.name} · ${wallet.address}` : 'Choose a browser wallet';
  $('connected-address').textContent = wallet.address ? `Connected ${wallet.address}` : 'Wallet disconnected · website session remains separate';
  $('connected-address').classList.add('wrap-address');
  const notice = $('wallet-notice');
  notice.replaceChildren();
  if (!$('wallet-setup').hidden) { notice.hidden = true; }
  else if ((!wallet.address && ['vaults', 'positions', 'security'].includes(view)) || (wallet.address && !user.wallets.includes(wallet.address))) {
    notice.hidden = false;
    notice.append(element('span', '', message || (wallet.address ? 'Link this wallet in Security to use it with your account.' : view === 'security' ? 'Connect a linked wallet to continue.' : 'Connect a wallet to use your tokens.')));
    if (!wallet.address) {
      const connectButton = button('Connect wallet', () => $('connect-wallet').click(), true);
      connectButton.disabled = wallet.connecting;
      notice.append(connectButton);
    }
    if (wallet.address && !user.wallets.includes(wallet.address)) {
      const link = element('a', 'text-link', 'Open Security ↗'); link.href = '/security'; notice.append(link);
    }
  } else if (message) { notice.hidden = false; notice.textContent = message; }
  else notice.hidden = true;
  if (view === 'admin' && snapshot) {
    $('contract-authority').textContent = wallet.address === snapshot.contract_owner ? 'Owner connected' : 'Owner signature required';
  }
}
function item(title, subtitle, value = '', symbol = 'layers') {
  const row = element('div', 'item'), content = element('div', 'item-content');
  content.append(element('strong', '', title), element('small', '', subtitle));
  const mark = element('span', 'item-icon'); mark.append(icon(symbol)); row.append(mark, content);
  if (value) {
    const amountNode = element('div', 'item-value'); amountNode.append(element('strong', '', value)); row.append(amountNode);
  }
  return row;
}
function chainLink(txHash) {
  if (config.explorer_url) {
    const link = element('a', 'text-link mono', `${txHash.slice(0, 10)}…${txHash.slice(-6)} ↗`);
    link.href = `${config.explorer_url}/tx/${txHash}`; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  }
  const node = element('details', 'tx-details');
  node.append(element('summary', '', `${txHash.slice(0, 8)}…${txHash.slice(-4)} · transaction`), element('p', 'mono wrap-address', txHash));
  return node;
}
function activityEvents(target, events, audit = false) {
  target.replaceChildren();
  if (!events.length) { emptyState(target, 'No activity yet', audit ? 'Account activity will appear here.' : 'Confirmed transactions will appear here.'); return; }
  for (const event of events) {
    const title = event.kind.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ');
    const row = item(title, audit ? dateString(event.created_at) : `Block ${event.block_number}`, '', audit ? 'shield' : 'arrow-up-right');
    if (event.tx_hash) row.querySelector('.item-content').append(chainLink(event.tx_hash));
    if (audit && event.details?.target) row.querySelector('.item-content').append(disclosure('Account details', element('small', 'mono wrap-address', event.details.target)));
    target.append(row);
  }
}
function financialReady() {
  if (!snapshot || Date.now() - Date.parse(snapshot.block.observed_at) > 30000) throw new Error('Refresh confirmed blockchain data before preparing a transaction.');
}
function preview(title, details, warning, callback) {
  financialReady();
  if (transactionBusy) throw new Error('Wait for the current wallet operation to complete.');
  prepared = callback;
  previewFocus = document.activeElement;
  $('transaction-title').textContent = title;
  const lines = [
    ['Network', `${config.network} · chain ${config.chain_id}`],
    ['Wallet signer', wallet.address || 'Connect a linked wallet'],
    ['Vault contract', config.vault_address], ['HVT contract', `${config.token_address} · HVT`], ...details,
  ];
  $('transaction-preview').replaceChildren(...lines.map(([label, value]) => detail(label, value)));
  $('transaction-warning').textContent = warning;
  $('transaction-status').replaceChildren();
  $('confirm-transaction').disabled = false; $('confirm-transaction').hidden = false;
  $('confirm-transaction').textContent = 'Continue in wallet';
  $('cancel-transaction').textContent = 'Cancel';
  $('transaction-dialog').showModal();
}

async function verifyPending(pending, attempts = 1) {
  for (let step = 0; step < attempts; step++) {
    const result = await api('/transactions/verify', {method: 'POST', body: {tx_hash: pending.hash, kind: pending.kind}});
    if (result.state === 'reverted') { sessionStorage.removeItem('hv_pending'); throw new Error('Transaction reverted on-chain. Its financial action did not complete.'); }
    if (result.state === 'confirmed') { sessionStorage.removeItem('hv_pending'); return result; }
    $('transaction-status').textContent = `Pending · ${result.confirmations || 0}/${config.confirmations} confirmations. Hash retained in this tab.`;
    if (step + 1 < attempts) await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Transaction remains pending. Refresh to verify its receipt; do not submit a duplicate action.');
}
async function send(context, kind, transactionFactory) {
  await wallet.assertContext(context.generation, context.address);
  $('transaction-status').textContent = 'Waiting for your wallet approval…';
  const transaction = await transactionFactory();
  const pending = {hash: transaction.hash, kind, user: user.id, chain: config.chain_id};
  sessionStorage.setItem('hv_pending', JSON.stringify(pending));
  $('transaction-status').replaceChildren(element('span', '', 'Submitted. Waiting for a successful receipt and server event verification. '), chainLink(transaction.hash));
  const receipt = await transaction.wait(config.confirmations, 120000);
  if (!receipt || receipt.status !== 1) throw new Error('Transaction reverted. No successful settlement was verified.');
  const verified = await verifyPending(pending, 3);
  $('transaction-status').replaceChildren(element('span', '', 'Confirmed by successful receipt and matching contract event. '), chainLink(transaction.hash));
  return verified;
}
async function exactApproval(context, value) {
  const existing = await context.token.allowance(context.address, config.vault_address);
  if (existing !== value) await send(context, 'approve', () => context.token.approve(config.vault_address, value));
  await wallet.assertContext(context.generation, context.address);
}
$('confirm-transaction').addEventListener('click', async () => {
  if (!prepared || transactionBusy) return;
  transactionBusy = true; $('confirm-transaction').disabled = true; $('cancel-transaction').disabled = true;
  try {
    await prepared();
    $('confirm-transaction').hidden = true; $('cancel-transaction').textContent = 'Close';
    toast('Transaction confirmed. Blockchain data refreshed.');
    await refresh();
  } catch (error) { $('transaction-status').textContent = walletError(error); }
  finally {
    transactionBusy = false; prepared = null; $('cancel-transaction').disabled = false;
    $('confirm-transaction').hidden = true; $('cancel-transaction').textContent = 'Close';
  }
});
$('cancel-transaction').addEventListener('click', () => { if (!transactionBusy) { $('transaction-dialog').close(); prepared = null; } });
$('transaction-dialog').addEventListener('cancel', event => { if (transactionBusy) event.preventDefault(); });
$('transaction-dialog').addEventListener('close', () => {
  prepared = null;
  // Refresh can replace the preview's original button. Keep focus in the workspace.
  const target = previewFocus?.isConnected && previewFocus.getClientRects().length ? previewFocus : $('main');
  target.focus({preventScroll: true}); previewFocus = null;
});
$('transaction-dialog').querySelector('.dialog-close').addEventListener('click', event => { if (transactionBusy) event.preventDefault(); });

function positionState(position) { return position.settled ? 'Settled' : position.mature ? 'Ready to withdraw' : 'Locked'; }
function renderOverview() {
  $('principal-total').textContent = tokens(snapshot.totals.principal);
  $('reward-total').textContent = tokens(snapshot.totals.rewards);
  $('maturity-total').textContent = tokens(snapshot.totals.at_maturity);
  $('overview-positions').replaceChildren();
  const active = snapshot.positions.filter(position => !position.settled);
  if (!active.length) emptyState($('overview-positions'), 'Your next position starts here', user.wallets.length ? 'Choose a vault to get started.' : 'Link a wallet to view your positions.', user.wallets.length ? '/vaults' : '/security', user.wallets.length ? 'Explore vaults ↗' : 'Link a wallet ↗');
  for (const position of active.slice(0, 5)) {
    $('overview-positions').append(item(`Position #${position.id} · ${position.duration / 86400}-day lock`, `${positionState(position)} · maturity ${dateTime(position.matures_at)}`, `${tokens(position.principal)} HVT`));
  }
  activityEvents($('recent-activity'), snapshot.activity.slice(0, 5));
}

function renderOffers() {
  $('offers').replaceChildren();
  if (!snapshot.offers.length) { empty($('offers'), 'No offers have been created on this contract.'); return; }
  for (const offer of snapshot.offers) {
    const minimumReward = BigInt(offer.minimum) * BigInt(offer.apr_bps) * BigInt(offer.duration) / (10000n * 365n * 86400n);
    const underfunded = minimumReward > BigInt(snapshot.reserves.uncommitted);
    const full = BigInt(offer.remaining_capacity) < BigInt(offer.minimum);
    const closed = snapshot.paused || !offer.open || full || underfunded;
    const card = element('article', 'offer'), top = element('div', 'offer-top');
    const status = snapshot.paused ? 'Paused' : !offer.open ? 'Closed' : underfunded ? 'Underfunded' : full ? 'Full' : 'Open';
    top.append(element('h2', '', `${offer.duration / 86400}-day lock`), element('span', `badge${closed ? ' badge-warning' : ''}`, status));
    const rewardRate = element('div', 'offer-rate', rate(offer.apr_bps));
    rewardRate.append(element('small', '', 'Annualized simple reward'));
    const details = element('dl', 'detail-list');
    details.append(detail('Deposit range', `${tokens(offer.minimum)}–${tokens(offer.maximum)} HVT`), detail('Capacity left', `${tokens(offer.remaining_capacity)} HVT`));
    const form = element('form', 'form-stack'), label = element('label', '', 'Amount (HVT)'), input = element('input');
    input.setAttribute('aria-label', `Deposit amount (HVT) · offer #${offer.id}`);
    input.inputMode = 'decimal'; input.placeholder = 'e.g. 100'; input.required = true; input.maxLength = 60;
    label.append(input); const submit = element('button', 'button full-width', 'Preview deposit'); submit.type = 'submit'; submit.disabled = closed;
    form.append(label, submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        financialReady(); const value = amount(input.value);
        const quote = await api('/quote', {method: 'POST', body: {offer_id: offer.id, amount: value.toString()}});
        if (!quote.can_deposit) throw new Error(quote.reason);
        await wallet.context(user);
        preview(`Deposit into offer #${offer.id}`, [
          ['Principal', `${tokens(value, true)} HVT`], ['Fixed reward', `${tokens(quote.reward, true)} HVT`],
          ['At maturity', `${tokens(quote.total, true)} HVT`], ['Lock', `${quote.offer.duration / 86400} days`],
          ['Annualized simple rate', rate(quote.offer.apr_bps)], ['Estimated maturity', dateTime(quote.estimated_maturity)],
          ['Approval limit', `${tokens(value, true)} HVT · exact amount`],
        ], 'The full reward comes from the sponsor reserve. No early withdrawal. Exact maturity is the mined deposit timestamp plus the lock duration. Gas is additional test ETH.', async () => {
          const context = await wallet.context(user);
          const latestQuote = await api('/quote', {method: 'POST', body: {offer_id: offer.id, amount: value.toString()}});
          if (!latestQuote.can_deposit || latestQuote.offer.duration !== quote.offer.duration || latestQuote.offer.apr_bps !== quote.offer.apr_bps) throw new Error('Offer terms or funding changed. Close this preview, refresh and review again.');
          await exactApproval(context, value);
          await send(context, 'deposit', () => context.vault.deposit(BigInt(offer.id), value, quote.offer.duration, quote.offer.apr_bps));
        });
      } catch (error) { report(error); }
      finally { submit.disabled = closed; }
    });
    const emblem = element('span', 'offer-emblem'); emblem.append(icon('vault'));
    const funding = element('dl', 'detail-list'); funding.append(detail('Shared reward reserve', `${tokens(snapshot.reserves.uncommitted, true)} HVT`));
    card.append(emblem, top, rewardRate, details, disclosure('Funding details', funding), form, element('p', 'offer-foot', 'No early withdrawal.')); $('offers').append(card);
  }
  const faucet = element('div', 'faucet-panel'), copy = element('div'), text = element('div');
  text.append(element('h3', '', 'Get HVT'), element('p', '', 'Claim 1,000 HVT every hour.'));
  copy.append(icon('spark'), text); faucet.append(copy);
  faucet.append(button('Get HVT', action(async () => {
    await wallet.context(user);
    preview('Get HVT', [['Amount', '1,000 HVT']], 'ETH is required for gas.', async () => {
      const context = await wallet.context(user); await send(context, 'faucet', () => context.token.faucet());
    });
  })));
  $('offers').append(faucet);
}

function renderPositions() {
  const target = $('position-list'); target.replaceChildren();
  const positions = snapshot.positions.filter(position => positionFilter === 'all' || (positionFilter === 'active' ? !position.settled : position.settled));
  $('position-count').textContent = `${positions.length} position${positions.length === 1 ? '' : 's'}`;
  if (!positions.length) { emptyState(target, 'No positions here yet', user.wallets.length ? 'Explore vaults to open a new position.' : 'Link a wallet to see your positions.', user.wallets.length ? '/vaults' : '/security', user.wallets.length ? 'Explore vaults ↗' : 'Link a wallet ↗'); return; }
  for (const position of positions) {
    const card = element('article', 'position-card'), top = element('div', 'position-top');
    top.append(element('h3', '', `Position #${position.id}`), element('span', `badge${position.settled ? ' badge-muted' : position.mature ? '' : ' badge-warning'}`, positionState(position)));
    const details = element('dl', 'detail-list');
    details.append(detail('Reserved reward', `${tokens(position.reward)} HVT`), detail('Maturity', dateTime(position.matures_at)), detail('Lock terms', `${position.duration / 86400} days · ${rate(position.apr_bps)}`));
    const exact = element('dl', 'detail-list');
    exact.append(detail('Principal', `${tokens(position.principal, true)} HVT`), detail('Reserved reward', `${tokens(position.reward, true)} HVT`), detail('Settlement total', `${tokens(position.total, true)} HVT`), detail('Owner wallet', position.owner));
    const balance = element('div', 'position-amount', tokens(position.principal)); balance.append(element('small', '', 'HVT deposited'));
    const progress = element('progress', 'position-progress');
    progress.max = position.duration; progress.value = Math.min(position.duration, Math.max(0, snapshot.block.timestamp - position.deposited_at));
    progress.setAttribute('aria-label', `Position ${position.id} lock elapsed`);
    const actions = element('div', 'form-actions');
    const withdraw = button(position.settled ? 'Settled' : 'Withdraw at maturity', action(async () => {
      await wallet.context(user, position.owner);
      preview(`Settle position #${position.id}`, [['Principal', `${tokens(position.principal, true)} HVT`], ['Reserved reward', `${tokens(position.reward, true)} HVT`], ['Payout', `${tokens(position.total, true)} HVT`], ['Maturity', dateTime(position.matures_at)]], 'Only the original owner wallet can settle. Website roles do not grant withdrawal rights. Deposit pause does not restrict matured withdrawals.', async () => {
        const context = await wallet.context(user, position.owner);
        await send(context, 'withdraw', () => context.vault.withdraw(BigInt(position.id)));
      });
    }));
    withdraw.disabled = position.settled || !position.mature;
    if (!position.mature) withdraw.title = 'This position has not reached its on-chain maturity';
    actions.append(withdraw, button('Private note', action(async () => {
      const result = await api(`/positions/${position.id}/note`);
      notePosition = position.id; $('note-title').textContent = `Private note · position #${position.id}`;
      $('note-form').elements.note.value = result.note || ''; $('note-form').querySelector('.form-message').textContent = '';
      $('note-dialog').showModal();
    }), true));
    card.append(top, balance, details, progress, actions, disclosure('Exact amounts & wallet', exact)); target.append(card);
  }
}
for (const control of document.querySelectorAll('[data-position-filter]')) control.addEventListener('click', () => {
  positionFilter = control.dataset.positionFilter;
  for (const sibling of document.querySelectorAll('[data-position-filter]')) {
    const selected = sibling === control; sibling.classList.toggle('is-active', selected); sibling.setAttribute('aria-pressed', String(selected));
  }
  if (snapshot) renderPositions();
});
$('note-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, message = form.querySelector('.form-message'), submit = form.querySelector('button[type="submit"]');
  submit.disabled = true; message.textContent = ''; message.classList.remove('is-error');
  try { await api(`/positions/${notePosition}/note`, {method: 'PUT', body: {note: form.elements.note.value}}); message.textContent = 'Encrypted note saved.'; }
  catch (error) { message.textContent = walletError(error); message.classList.add('is-error'); }
  finally { submit.disabled = false; }
});
$('note-dialog').addEventListener('close', () => { $('note-form').reset(); notePosition = null; });

async function renderSecurity() {
  $('wallet-list').replaceChildren();
  if (!user.wallets.length) empty($('wallet-list'), 'No linked wallets yet.');
  for (const address of user.wallets) {
    const row = item(shortAddress(address), 'Ownership verified', '', 'wallet');
    row.querySelector('.item-content').append(disclosure('View address', element('small', 'mono wrap-address', address)));
    const actions = element('div', 'item-actions');
    actions.append(button('Unlink', action(async () => {
      if (!confirm('Unlink this wallet from website access? Its on-chain positions and owner remain unchanged.')) return;
      const result = await api(`/security/wallets/${address}`, {method: 'DELETE'}); user = result.user;
      account(); walletState(); await renderSecurity();
    }), true)); row.append(actions); $('wallet-list').append(row);
  }
  passwordFormState();
  const [sessions, activity] = await Promise.all([api('/security/sessions'), api('/security/activity')]);
  $('session-list').replaceChildren();
  for (const session of sessions.sessions) {
    const row = item(session.current ? 'This browser' : 'Other browser', `Last used ${dateString(session.last_seen)}`, '', 'shield');
    row.querySelector('.item-content').append(disclosure('Browser details', element('small', '', session.agent)));
    const actions = element('div', 'item-actions');
    actions.append(button(session.current ? 'Sign out' : 'Revoke session', action(async () => {
      await api(`/security/sessions/${session.id}`, {method: 'DELETE'});
      if (session.current) location.assign('/login'); else { toast('Session revoked.'); await renderSecurity(); }
    }), true)); row.append(actions); $('session-list').append(row);
  }
  activityEvents($('security-activity'), activity.events, true);
}
async function setupWallet() {
  if (!wallet || linkingWallet || wallet.connecting || passwordBusy) return;
  $('error-banner').hidden = true;
  if (!wallet.address) { await wallet.connect(); walletState(); return; }
  if (user.wallets.includes(wallet.address)) return;
  linkingWallet = true; walletSetupState();
  try {
    const result = await wallet.authenticate('link'); user = result.user; account(); walletState();
    toast('Wallet linked. You can now use vaults.');
    if (view === 'security') await renderSecurity(); else await refresh();
  } finally { linkingWallet = false; walletSetupState(); }
}
for (const id of ['wallet-setup-action', 'link-wallet']) $(id).addEventListener('click', async event => {
  await action(setupWallet)(event); walletSetupState();
});
$('wallet-reauth').addEventListener('click', action(async () => {
  if (!wallet?.address) throw new Error('Connect a linked wallet explicitly before authenticating.');
  await wallet.authenticate('reauth'); toast('Recent linked-wallet authentication verified.'); await renderSecurity();
}));
$('reauth').addEventListener('click', action(async () => {
  await api('/auth/reauth', {method: 'POST', body: {password: $('password-form').elements.current_password.value}});
  $('password-form').elements.current_password.value = ''; toast('Recent password authentication verified.');
}));
$('password-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, message = form.querySelector('.form-message');
  if (!user || passwordBusy || linkingWallet) return;
  const data = Object.fromEntries(new FormData(form)), setup = !user.has_password;
  message.textContent = ''; message.classList.remove('is-error');
  form.elements.confirm_password.removeAttribute('aria-invalid');
  if (data.new_password !== data.confirm_password) {
    message.textContent = 'Passwords do not match.'; message.classList.add('is-error');
    form.elements.confirm_password.setAttribute('aria-invalid', 'true'); form.elements.confirm_password.focus(); return;
  }
  passwordBusy = setup ? 'wallet' : 'save'; walletSetupState();
  try {
    if (setup) {
      if (!wallet?.address || !user.wallets.includes(wallet.address)) throw new Error('Connect a linked wallet to set up email sign-in.');
      await wallet.authenticate('reauth'); passwordBusy = 'save'; walletSetupState();
    }
    const body = {current_password: setup ? '' : data.current_password, new_password: data.new_password};
    if (setup) body.email = data.email;
    const result = await api('/auth/password', {method: 'POST', body});
    user = result.user; form.reset(); account();
    for (const name of ['new_password', 'confirm_password']) form.elements[name].type = 'password';
    $('security-show-password').setAttribute('aria-label', 'Show new password'); $('security-show-password').setAttribute('aria-pressed', 'false');
    message.textContent = setup ? 'Email sign-in is ready. Use email or your linked wallet.' : 'Password updated. Other sessions signed out.';
    await renderSecurity();
  } catch (error) { message.textContent = walletError(error); message.classList.add('is-error'); }
  finally { passwordBusy = null; walletSetupState(); }
});
$('security-show-password').addEventListener('click', () => {
  const form = $('password-form'), showing = form.elements.new_password.type === 'password';
  for (const name of ['new_password', 'confirm_password']) form.elements[name].type = showing ? 'text' : 'password';
  $('security-show-password').setAttribute('aria-label', showing ? 'Hide new password' : 'Show new password');
  $('security-show-password').setAttribute('aria-pressed', String(showing));
});
$('revoke-others').addEventListener('click', action(async () => {
  const result = await api('/security/sessions/revoke-others', {method: 'POST'});
  toast(`${result.count} other session(s) revoked.`); await renderSecurity();
}));

async function renderAdmin() {
  $('reserve-total').textContent = tokens(snapshot.reserves.uncommitted);
  $('liability-total').textContent = tokens(snapshot.reserves.liabilities);
  $('contract-total').textContent = tokens(snapshot.reserves.balance);
  $('coverage').textContent = 'Principal & rewards covered';
  $('contract-owner').textContent = `Owner · ${shortAddress(snapshot.contract_owner)}`;
  $('contract-owner').title = snapshot.contract_owner;
  $('pause-deposits').textContent = snapshot.paused ? 'Resume new deposits' : 'Pause new deposits';
  walletState();
  let accept = $('accept-ownership');
  if (snapshot.pending_owner !== '0x0000000000000000000000000000000000000000') {
    if (!accept) {
      accept = button('Accept pending contract ownership', action(async () => {
        await wallet.context(user, snapshot.pending_owner);
        preview('Accept contract ownership', [['Pending owner', snapshot.pending_owner]], 'This changes contract administration only. Website roles and existing positions do not change.', async () => {
          const context = await wallet.context(user, snapshot.pending_owner); await send(context, 'ownership', () => context.vault.acceptOwnership());
        });
      }), true); accept.id = 'accept-ownership'; $('contract-owner').after(accept);
    }
  } else if (accept) accept.remove();
  $('admin-offers').replaceChildren();
  for (const offer of snapshot.offers) {
    const row = item(`${offer.duration / 86400} days · ${rate(offer.apr_bps)}`, `${offer.open ? 'Open' : 'Closed'} · ${tokens(offer.remaining_capacity)} HVT left`);
    const actions = element('div', 'item-actions');
    actions.append(button(offer.open ? 'Close offer' : 'Open offer', action(async () => {
      await wallet.context(user, snapshot.contract_owner);
      preview(`${offer.open ? 'Close' : 'Open'} offer #${offer.id}`, [['Future deposits', offer.open ? 'Closed' : 'Open']], 'Existing positions and withdrawals are unaffected.', async () => {
        const context = await wallet.context(user, snapshot.contract_owner); await send(context, 'availability', () => context.vault.setOfferOpen(BigInt(offer.id), !offer.open));
      });
    }), true), button('Edit future terms', () => {
      const form = $('offer-form'); form.dataset.offerId = offer.id;
      form.elements.days.value = offer.duration / 86400; form.elements.apr_bps.value = offer.apr_bps;
      form.elements.minimum.value = tokens(offer.minimum, true).replaceAll(',', '');
      form.elements.maximum.value = tokens(offer.maximum, true).replaceAll(',', '');
      form.elements.capacity.value = tokens(offer.capacity, true).replaceAll(',', '');
      form.querySelector('button[type="submit"]').textContent = `Preview update · offer #${offer.id}`;
      $('offer-editor').open = true; $('offer-editor-title').textContent = `Edit offer #${offer.id}`; $('cancel-offer-edit').hidden = false;
      form.elements.days.focus(); form.scrollIntoView({block: 'center'});
    }, true)); row.append(actions); $('admin-offers').append(row);
  }
  $('liability-list').replaceChildren();
  if (!snapshot.liabilities.length) empty($('liability-list'), 'No outstanding principal or committed reward liabilities.');
  for (const position of snapshot.liabilities) $('liability-list').append(item(`Position #${position.id} · ${shortAddress(position.owner)}`, `${positionState(position)} · maturity ${dateTime(position.matures_at)}`, `${tokens(position.total)} HVT`));
  for (const event of snapshot.contract_events.slice(0, 15)) {
    const row = item(event.kind.replace(/([a-z])([A-Z])/g, '$1 $2'), `Block ${event.block_number}`, '', 'arrow-up-right');
    row.querySelector('.item-content').append(chainLink(event.tx_hash)); $('admin-audit').append(row);
  }
}

async function renderAdminAccounts() {
  const [directory, audit] = await Promise.all([api('/admin/users'), api('/admin/audit')]);
  $('user-directory-count').textContent = directory.total > directory.users.length ? `LATEST ${directory.users.length} OF ${directory.total}` : `${directory.total} ACCOUNTS`;
  $('user-list').replaceChildren();
  for (const member of directory.users) {
    const row = item(member.name, `${member.email || 'Wallet account'} · ${member.role} · ${member.disabled ? 'disabled' : 'active'}`, '', 'shield');
    row.dataset.search = `${member.name} ${member.email || ''}`.toLowerCase();
    if (member.id !== user.id) {
      const actions = element('div', 'item-actions');
      for (const [label, body] of [[member.disabled ? 'Enable account' : 'Disable account', {disabled: !member.disabled}], [member.role === 'admin' ? 'Remove admin role' : 'Make administrator', {role: member.role === 'admin' ? 'user' : 'admin'}]]) {
        actions.append(button(label, action(async () => {
          if (!confirm(`${label} for ${member.name}? Their website sessions will be revoked. On-chain rights remain unchanged.`)) return;
          await api(`/admin/users/${member.id}`, {method: 'PATCH', body}); toast('Website account updated.'); await refresh();
        }), true));
      }
      actions.append(button('Revoke sessions', action(async () => { await api(`/admin/users/${member.id}/revoke-sessions`, {method: 'POST'}); toast('User sessions revoked.'); await refresh(); }), true));
      const controls = element('details', 'user-controls'), toggle = element('summary', 'icon-button');
      toggle.setAttribute('aria-label', `Manage ${member.name}`); toggle.append(icon('more')); controls.append(toggle, actions); row.append(controls);
    }
    $('user-list').append(row);
  }
  activityEvents($('admin-audit'), audit.events, true);
  filterAccounts();
}
function filterAccounts() {
  const query = $('user-search').value.trim().toLowerCase(); let shown = 0;
  for (const row of $('user-list').children) { row.hidden = !row.dataset.search.includes(query); if (!row.hidden) shown++; }
  $('user-search-empty').hidden = shown !== 0;
}
$('user-search').addEventListener('input', filterAccounts);
const adminTabs = [...document.querySelectorAll('.admin-tabs [role="tab"]')];
function selectAdminTab(selected) {
  for (const tab of adminTabs) {
    const active = tab === selected; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
    $(tab.getAttribute('aria-controls')).hidden = !active;
  }
}
for (const tab of adminTabs) {
  tab.addEventListener('click', () => selectAdminTab(tab));
  tab.addEventListener('keydown', event => {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? adminTabs.length - 1 : (adminTabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : -1) + adminTabs.length) % adminTabs.length;
    selectAdminTab(adminTabs[index]); adminTabs[index].focus();
  });
}
function resetOfferEditor() {
  const form = $('offer-form'); form.reset(); delete form.dataset.offerId;
  form.querySelector('button[type="submit"]').textContent = 'Preview offer creation';
  $('offer-editor-title').textContent = 'Create an offer'; $('cancel-offer-edit').hidden = true;
}
$('cancel-offer-edit').addEventListener('click', resetOfferEditor);
$('fund-form').addEventListener('submit', action(async event => {
  event.preventDefault(); const value = amount(event.currentTarget.elements.amount.value); await wallet.context(user);
  preview('Sponsor reward funding', [['Sponsor amount', `${tokens(value, true)} HVT`], ['Approval limit', `${tokens(value, true)} HVT · exact amount`]], 'Funding becomes the shared reward reserve. The contract provides no sponsor or administrator withdrawal/sweep function.', async () => {
    const context = await wallet.context(user); await exactApproval(context, value); await send(context, 'fund', () => context.vault.fundRewards(value));
  });
}));
$('pause-deposits').addEventListener('click', action(async () => {
  await wallet.context(user, snapshot.contract_owner);
  preview(snapshot.paused ? 'Resume new deposits' : 'Pause new deposits', [['New deposit state', snapshot.paused ? 'Available for open, funded offers' : 'Paused']], 'Matured withdrawals remain available to position owners.', async () => {
    const context = await wallet.context(user, snapshot.contract_owner); await send(context, 'pause', () => context.vault.setDepositsPaused(!snapshot.paused));
  });
}));
$('offer-form').addEventListener('submit', action(async event => {
  event.preventDefault(); const form = event.currentTarget;
  await wallet.context(user, snapshot.contract_owner);
  const data = Object.fromEntries(new FormData(form));
  const days = Number(data.days), bps = Number(data.apr_bps);
  if (!Number.isInteger(days) || days < 1 || days > 365 || !Number.isInteger(bps) || bps < 0 || bps > 10000) throw new Error('Duration and annualized rate are outside supported bounds.');
  const minimum = amount(data.minimum), maximum = amount(data.maximum), capacity = amount(data.capacity);
  if (minimum > maximum || maximum > capacity) throw new Error('Minimum ≤ maximum per position ≤ capacity is required.');
  const editing = form.dataset.offerId;
  preview(editing ? `Update future offer #${editing}` : 'Create a future offer', [['Duration', `${days} days`], ['Annualized simple rate', rate(bps)], ['Per-position range', `${tokens(minimum, true)}–${tokens(maximum, true)} HVT`], ['Capacity', `${tokens(capacity, true)} HVT`]], 'Only future deposits use these terms. Existing locks and rewards are immutable. Deposits require separate reserve funding.', async () => {
    const context = await wallet.context(user, snapshot.contract_owner);
    await send(context, 'offer', () => editing ? context.vault.updateOffer(BigInt(editing), days * 86400, bps, minimum, maximum, capacity) : context.vault.createOffer(days * 86400, bps, minimum, maximum, capacity));
    resetOfferEditor(); $('offer-editor').open = false;
  });
}));

async function refresh() {
  if (refreshing) return;
  refreshing = true; $('refresh').disabled = true; $('error-banner').hidden = true;
  try {
    const session = await boot(); if (!session.user) { location.assign('/login'); return; }
    user = session.user; account();
    // Website security controls remain usable when the blockchain is offline.
    if (view === 'security') await renderSecurity();
    if (view === 'admin') await renderAdminAccounts();
    if (!config) {
      config = await api('/config'); wallet = new Wallet(config);
      wallet.addEventListener('change', event => walletState(event.detail));
      wallet.addEventListener('status', event => walletState(event.detail));
      await wallet.resume();
      $('network-label').replaceChildren(element('span', 'status-dot'), document.createTextNode(config.chain_id === 31337 ? 'Anvil' : 'Sepolia'));
      document.querySelector('.environment').lastChild.textContent = config.chain_id === 31337 ? 'Anvil' : 'Sepolia';
      walletState();
    }
    if (view === 'security') {
      $('freshness').textContent = 'Account data up to date'; return;
    }
    snapshot = await api(view === 'admin' ? '/admin/snapshot' : '/snapshot');
    $('live-status').classList.remove('is-error', 'is-stale');
    $('freshness').textContent = `Confirmed block ${snapshot.block.number} · Updated ${new Date(snapshot.block.observed_at).toLocaleTimeString()}`;
    if (view === 'overview') renderOverview();
    if (view === 'vaults') renderOffers();
    if (view === 'positions') renderPositions();
    if (view === 'admin') await renderAdmin();
  } catch (error) {
    snapshot = null; report(error); $('live-status').classList.add('is-error');
    $('freshness').textContent = view === 'security' ? 'Account controls are separate · wallet configuration unavailable' : 'Data unavailable · no current balances or confirmations verified';
    for (const id of ['principal-total', 'reward-total', 'maturity-total', 'reserve-total', 'liability-total', 'contract-total']) $(id).textContent = '—';
    for (const id of view === 'overview' ? ['overview-positions', 'recent-activity'] : view === 'vaults' ? ['offers'] : view === 'positions' ? ['position-list'] : []) empty($(id), 'Blockchain data unavailable. Refresh when the RPC and index recover.');
    if (view === 'admin') {
      $('coverage').textContent = 'Reserve coverage could not be verified';
      $('contract-owner').textContent = 'Contract owner unavailable';
      $('contract-authority').textContent = 'CONTRACT DATA UNAVAILABLE';
      $('accept-ownership')?.remove();
      for (const id of ['admin-offers', 'liability-list']) empty($(id), 'Refresh after the blockchain connection recovers.');
    }
  } finally { refreshing = false; $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', refresh);
$('connect-wallet').addEventListener('click', action(async () => {
  if (!wallet) throw new Error('Contract configuration unavailable. Refresh the page after setup.');
  await wallet.connect(); walletState();
}));
$('logout').addEventListener('click', action(async () => { await api('/auth/logout', {method: 'POST'}); sessionStorage.removeItem('hv_pending'); sessionStorage.removeItem('hv_wallet_connection'); location.assign('/login'); }));
await refresh();
if (config && user) {
  let pending;
  try { pending = JSON.parse(sessionStorage.getItem('hv_pending') || 'null'); } catch { sessionStorage.removeItem('hv_pending'); }
  if (pending && pending.user === user.id && pending.chain === config.chain_id) {
    try { await verifyPending(pending); toast('Previously submitted transaction is now verified as confirmed.'); await refresh(); }
    catch (error) { report(error); }
  }
}
setInterval(() => {
  if (snapshot && Date.now() - Date.parse(snapshot.block.observed_at) > 30000) {
    $('live-status').classList.add('is-stale'); $('freshness').textContent = `Stale read from block ${snapshot.block.number}. Refresh before transacting.`;
  }
  if (!document.hidden && !transactionBusy && !$('transaction-dialog').open && !$('note-dialog').open && view !== 'security') refresh();
}, 15000);

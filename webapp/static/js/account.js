// Account access lives here; this module never reads balances or prepares transactions.
import {api, boot, $, element, empty, icon, disclosure, button, shortAddress, dateString} from './api.js?v=2';
import {Wallet, walletError} from './wallet.js?v=5';
import {confirmFactor, reauthenticate} from './security.js?v=1';

let user = null, wallet = null, linkingWallet = false, passwordBusy = null, working = false;
let toastTimer = null;
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 7000);
}
function report(error) {
  if (['WALLET_SELECTION_CANCELLED', 'AUTH_CANCELLED'].includes(error.code)) return;
  if (error.status === 401) { location.assign('/login'); return; }
  $('error-banner').textContent = walletError(error); $('error-banner').hidden = false;
}
function action(callback) {
  return async event => {
    event?.preventDefault();
    if (!user || working || passwordBusy || event?.currentTarget.disabled) return;
    working = true; walletSetupState(); $('error-banner').hidden = true;
    try { await callback(event); }
    catch (error) { report(error); }
    finally { working = false; walletSetupState(); }
  };
}
async function recentAction(callback) {
  try { return await callback(); }
  catch (error) {
    if (!error.reauthRequired) throw error;
    await reauthenticate(user, wallet); return callback();
  }
}
function account() {
  $('account-identity').textContent = user.email || user.name;
  $('mfa-status').textContent = user.mfa_enabled ? 'Enabled' : 'Off';
  $('mfa-description').textContent = user.mfa_enabled ? `Email and wallet sign-in are protected. ${user.recovery_codes_remaining} recovery codes left.` : 'Add an authenticator to protect email and wallet sign-in.';
  $('mfa-start').hidden = !!user.mfa_enabled || !$('mfa-setup-form').hidden;
  $('mfa-replace-codes').hidden = $('mfa-disable').hidden = !user.mfa_enabled;
  walletSetupState();
}
function walletSetupState() {
  const linked = !!wallet?.address && user?.wallets.includes(wallet.address);
  const busy = working || !!passwordBusy || linkingWallet || !!wallet?.connecting;
  for (const id of ['mfa-start', 'mfa-replace-codes', 'mfa-disable', 'mfa-enable', 'mfa-cancel-setup', 'verify-account', 'revoke-others', 'logout']) $(id).disabled = !user || busy;
  $('connect-wallet').disabled = !wallet || busy;
  $('link-wallet').textContent = linkingWallet ? 'Waiting for signature…' : wallet?.connecting ? 'Connecting…' : wallet?.address ? 'Link wallet' : 'Connect wallet';
  $('link-wallet').disabled = !wallet || busy; $('link-wallet').hidden = !wallet?.address || linked;
  passwordFormState();
}
function walletState(message = '') {
  $('wallet-button-label').textContent = wallet?.address ? shortAddress(wallet.address) : 'Connect wallet';
  $('connected-address').textContent = message || (wallet?.address ? `Connected ${shortAddress(wallet.address)}` : 'Wallet disconnected');
  walletSetupState();
}
function activityEvents(target, events) {
  target.replaceChildren();
  if (!events.length) { empty(target, 'No security activity yet.'); return; }
  for (const event of events) target.append(item(event.kind.replaceAll('_', ' '), dateString(event.created_at), '', 'shield'));
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
  $('save-password').disabled = !!passwordBusy || working || linkingWallet || (setup && (!wallet?.address || !user.wallets.includes(wallet.address)));
  $('save-password').setAttribute('aria-busy', String(!!passwordBusy));
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
async function renderSecurity() {
  $('wallet-list').replaceChildren();
  if (!user.wallets.length) empty($('wallet-list'), 'No linked wallets yet.');
  for (const address of user.wallets) {
    const row = item(shortAddress(address), 'Ownership verified', '', 'wallet');
    row.querySelector('.item-content').append(disclosure('View address', element('small', 'mono wrap-address', address)));
    const actions = element('div', 'item-actions');
    actions.append(button('Unlink', action(async () => {
      if (!confirm('Unlink this wallet from website access? Its on-chain positions and owner remain unchanged.')) return;
      const result = await recentAction(() => api(`/security/wallets/${address}`, {method: 'DELETE'})); user = result.user;
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
$('password-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, message = form.querySelector('.form-message');
  if (!user || working || passwordBusy || linkingWallet) return;
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
      await confirmFactor(user, code => wallet.authenticate('reauth', code)); passwordBusy = 'save'; walletSetupState();
    }
    const body = {current_password: setup ? '' : data.current_password, new_password: data.new_password};
    if (setup) body.email = data.email;
    const result = setup ? await api('/auth/password', {method: 'POST', body}) : await confirmFactor(user, code => api('/auth/password', {method: 'POST', body: {...body, ...(code ? {mfa_code: code} : {})}}));
    user = result.user; form.reset(); clearSetup(); account();
    for (const name of ['new_password', 'confirm_password']) form.elements[name].type = 'password';
    $('security-show-password').setAttribute('aria-label', 'Show new password'); $('security-show-password').setAttribute('aria-pressed', 'false');
    message.textContent = setup ? 'Email sign-in is ready. Use email or your linked wallet.' : 'Password updated. Other sessions signed out.';
    await renderSecurity();
  } catch (error) { if (error.code !== 'AUTH_CANCELLED') { message.textContent = walletError(error); message.classList.add('is-error'); } }
  finally { passwordBusy = null; walletSetupState(); }
});
$('security-show-password').addEventListener('click', () => {
  const form = $('password-form'), showing = form.elements.new_password.type === 'password';
  for (const name of ['new_password', 'confirm_password']) form.elements[name].type = showing ? 'text' : 'password';
  $('security-show-password').setAttribute('aria-label', showing ? 'Hide new password' : 'Show new password');
  $('security-show-password').setAttribute('aria-pressed', String(showing));
});
$('revoke-others').addEventListener('click', action(async () => {
  const result = await recentAction(() => api('/security/sessions/revoke-others', {method: 'POST'}));
  toast(`${result.count} other session(s) revoked.`); await renderSecurity();
}));

$('verify-account').addEventListener('click', action(async () => {
  await reauthenticate(user, wallet); toast('Identity verified.');
}));
$('connect-wallet').addEventListener('click', action(async () => { await wallet.connect(); walletState(); }));
$('link-wallet').addEventListener('click', action(async () => {
  if (!wallet.address) { await wallet.connect(); walletState(); return; }
  linkingWallet = true; walletSetupState();
  try {
    const result = await recentAction(() => wallet.authenticate('link'));
    user = result.user; clearSetup(); account(); walletState(); await renderSecurity(); toast('Wallet linked.');
  } finally { linkingWallet = false; walletSetupState(); }
}));
$('logout').addEventListener('click', action(async () => {
  await api('/auth/logout', {method: 'POST'});
  sessionStorage.removeItem('hv_pending'); sessionStorage.removeItem('hv_wallet_connection'); location.assign('/login');
}));

function clearSetup() {
  $('mfa-setup-form').reset(); $('mfa-setup-form').hidden = true;
  $('mfa-qr').removeAttribute('src'); $('mfa-qr').hidden = true; $('mfa-manual').open = false;
  $('mfa-secret').textContent = ''; $('mfa-setup-form').querySelector('.form-message').textContent = '';
}
// Keep enrollment usable if the image cannot load, without exposing the key by default.
$('mfa-qr').addEventListener('error', () => {
  if ($('mfa-setup-form').hidden) return;
  $('mfa-qr').hidden = true; $('mfa-manual').open = true;
});
function showCodes(result) {
  user = result.user; clearSetup(); account();
  $('mfa-codes').textContent = result.recovery_codes.join('\n'); $('mfa-recovery').hidden = false;
  $('mfa-recovery').scrollIntoView({block: 'nearest'}); $('mfa-download-codes').focus();
}
$('mfa-start').addEventListener('click', action(async () => {
  await reauthenticate(user, wallet);
  const result = await api('/security/mfa/setup', {method: 'POST'});
  clearSetup();
  $('mfa-secret').textContent = result.secret; $('mfa-setup-form').hidden = false;
  if (result.qr_data_url?.startsWith('data:image/png;base64,')) {
    $('mfa-qr').src = result.qr_data_url; $('mfa-qr').hidden = false;
  } else $('mfa-manual').open = true;
  account(); $('mfa-setup-form').elements.code.focus();
}));
$('mfa-setup-form').addEventListener('submit', async event => {
  event.preventDefault(); if (working || passwordBusy) return;
  working = true; walletSetupState();
  const message = event.currentTarget.querySelector('.form-message'); message.textContent = ''; message.classList.remove('is-error');
  try {
    const result = await api('/security/mfa/enable', {method: 'POST', body: {code: event.currentTarget.elements.code.value.trim()}});
    showCodes(result); await renderSecurity();
  } catch (error) { message.textContent = walletError(error); message.classList.add('is-error'); }
  finally { working = false; walletSetupState(); }
});
$('mfa-cancel-setup').addEventListener('click', action(async () => {
  await api('/security/mfa/setup', {method: 'DELETE'}); clearSetup(); account();
}));
$('mfa-copy-key').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('mfa-secret').textContent); toast('Setup key copied.'); }
  catch { toast('Select the setup key and copy it.'); }
});
$('mfa-replace-codes').addEventListener('click', action(async () => {
  await reauthenticate(user, wallet, 'recovery-codes');
  const result = await api('/security/mfa/recovery-codes', {method: 'POST', body: {}});
  showCodes(result); await renderSecurity();
}));
$('mfa-disable').addEventListener('click', action(async () => {
  await reauthenticate(user, wallet, 'disable');
  const result = await api('/security/mfa', {method: 'DELETE', body: {}});
  user = result.user; $('mfa-codes').textContent = ''; $('mfa-recovery').hidden = true; account(); await renderSecurity();
  toast('Two-factor authentication turned off.');
}));
$('mfa-download-codes').addEventListener('click', () => {
  const blob = new Blob([`HyperVaults recovery codes\nEach code works once. Keep this file private.\n\n${$('mfa-codes').textContent}\n`], {type: 'text/plain'});
  const url = URL.createObjectURL(blob), link = element('a');
  link.href = url; link.download = 'hypervaults-recovery-codes.txt'; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('mfa-dismiss-codes').addEventListener('click', () => { $('mfa-codes').textContent = ''; $('mfa-recovery').hidden = true; });

try {
  const session = await boot();
  if (!session.user) location.replace('/login');
  else {
    user = session.user; account(); await renderSecurity(); $('freshness').textContent = 'Account data up to date';
    // Chain outages affect wallet signatures only; password/MFA controls stay usable.
    try {
      const config = await api('/config'); wallet = new Wallet(config);
      wallet.addEventListener('change', () => walletState());
      wallet.addEventListener('status', event => walletState(event.detail));
      await wallet.resume(); walletState();
    } catch { $('connected-address').textContent = 'Wallet connection unavailable. Try again later.'; }
  }
} catch (error) { report(error); }


// Keep setup keys and recovery codes out of the browser's page history cache.
window.addEventListener('pagehide', () => { clearSetup(); $('mfa-codes').textContent = ''; $('mfa-recovery').hidden = true; $('password-form').reset(); });
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

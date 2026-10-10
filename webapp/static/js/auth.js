import {api, boot, $, element} from './api.js?v=2';
import {Wallet, walletError} from './wallet.js?v=5';

const view = location.pathname.slice(1), form = $('auth-form'), message = $('auth-message');
let resetToken = null, wallet = null, sessionReady = false, busy = null;
let mfaMode = null, resetBody = null, usingRecovery = false;
const titles = {login: 'Welcome back', register: 'Create an account', recover: 'Forgot your password?', reset: 'Set a new password'};
$('auth-title').textContent = titles[view] || titles.login;
document.title = `HyperVaults · ${$('auth-title').textContent}`;

function showField(id, shown, required = shown) {
  $(id).hidden = !shown;
  $(id).querySelector('input').required = required;
}
showField('name-label', view === 'register');
showField('confirm-label', ['register', 'reset'].includes(view));
showField('email-label', view !== 'reset');
showField('password-label', view !== 'recover');
$('password-hint').hidden = !['register', 'reset'].includes(view);
$('wallet-auth').hidden = !['login', 'register'].includes(view);
if (['register', 'reset'].includes(view)) {
  form.elements.password.minLength = 12;
  form.elements.password.autocomplete = 'new-password';
}
const submitLabel = {login: 'Sign in', register: 'Create account', recover: 'Send recovery link', reset: 'Reset password'}[view] || 'Sign in';
const pendingLabel = {login: 'Signing in…', register: 'Creating account…', recover: 'Sending link…', reset: 'Resetting password…'}[view] || 'Signing in…';
$('auth-submit').textContent = submitLabel;
$('recovery-link').hidden = view !== 'login';
$('auth-description').hidden = view === 'login';
if (view !== 'login') {
  $('auth-links').replaceChildren(element('a', 'text-link', 'Back to sign in'));
  $('auth-links').firstChild.href = '/login';
}
if (view === 'recover') $('auth-description').textContent = 'Enter your email for a recovery link.';
if (view === 'register') $('auth-description').textContent = 'Choose wallet or email access.';
if (view === 'reset') {
  resetToken = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', '/reset');
  $('auth-description').textContent = 'Your other sessions will be signed out.';
  if (!resetToken) { message.textContent = 'Open a valid recovery link to reset your password.'; message.classList.add('is-error'); $('auth-submit').disabled = true; }
}

function errorMessage(error) { message.textContent = walletError(error); message.classList.add('is-error'); }

function showMfa(mode = 'login') {
  mfaMode = mode; form.reset(); form.hidden = true;
  $('wallet-auth').hidden = $('auth-links').hidden = true;
  $('auth-title').textContent = 'Two-factor verification';
  $('auth-description').hidden = false;
  $('auth-description').textContent = 'Enter the code from your authenticator.';
  $('mfa-form').hidden = false;
  $('mfa-submit').textContent = mode === 'reset' ? 'Verify & reset password' : 'Verify & sign in';
  message.textContent = ''; message.classList.remove('is-error');
  setBusy(); $('mfa-form').elements.code.focus();
}

function setBusy(method = null) {
  busy = method;
  $('auth-submit').disabled = !sessionReady || !!busy || (view === 'reset' && !resetToken);
  $('siwe-login').disabled = !sessionReady || !!busy || !wallet;
  $('auth-submit').setAttribute('aria-busy', String(busy === 'email'));
  $('siwe-login').setAttribute('aria-busy', String(busy === 'wallet'));
  $('auth-submit').textContent = busy === 'email' ? pendingLabel : submitLabel;
  $('wallet-login-label').textContent = busy === 'wallet' ? 'Waiting for wallet…' : 'Continue with wallet';
  for (const id of ['mfa-submit', 'mfa-use-recovery', 'mfa-cancel']) $(id).disabled = !sessionReady || !!busy;
  $('mfa-submit').setAttribute('aria-busy', String(busy === 'mfa'));
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!sessionReady || busy || mfaMode || (view === 'reset' && !resetToken)) return;
  message.textContent = ''; message.classList.remove('is-error');
  const data = Object.fromEntries(new FormData(form));
  if (['register', 'reset'].includes(view) && data.password !== data.confirm) { errorMessage(new Error('Passwords do not match.')); return; }
  setBusy('email');
  try {
    if (view === 'register') await api('/auth/register', {method: 'POST', body: {email: data.email, password: data.password, name: data.name}});
    else if (view === 'recover') {
      const result = await api('/auth/recover', {method: 'POST', body: {email: data.email}});
      message.textContent = result.message; form.reset(); return;
    } else if (view === 'reset') {
      const result = await api('/auth/reset', {method: 'POST', body: {token: resetToken, password: data.password}});
      resetToken = null; form.reset(); message.textContent = result.message;
      $('auth-submit').hidden = true; return;
    } else {
      const result = await api('/auth/login', {method: 'POST', body: {email: data.email, password: data.password}});
      if (result.mfa_required) { showMfa(); return; }
    }
    form.reset(); location.assign('/overview');
  } catch (error) {
    if (view === 'reset' && error.mfaRequired) { resetBody = {token: resetToken, password: data.password}; showMfa('reset'); }
    else errorMessage(error);
  }
  finally { setBusy(); }
});
$('show-password').addEventListener('click', () => {
  const showing = form.elements.password.type === 'password';
  form.elements.password.type = showing ? 'text' : 'password';
  $('show-password').setAttribute('aria-label', showing ? 'Hide password' : 'Show password');
  $('show-password').setAttribute('aria-pressed', String(showing));
});
$('siwe-login').addEventListener('click', async () => {
  if (!sessionReady || busy || mfaMode || !wallet) return;
  setBusy('wallet'); message.classList.remove('is-error'); message.textContent = '';
  try {
    await wallet.connect(); message.textContent = 'Confirm the sign-in message in your wallet.';
    const result = await wallet.authenticate('login');
    if (result.mfa_required) showMfa(); else location.assign('/overview');
  }
  catch (error) { if (error.code !== 'WALLET_SELECTION_CANCELLED') errorMessage(error); else message.textContent = ''; }
  finally { setBusy(); }
});

$('mfa-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!sessionReady || busy || !mfaMode) return;
  const code = event.currentTarget.elements.code.value.trim();
  setBusy('mfa'); message.textContent = ''; message.classList.remove('is-error');
  try {
    if (mfaMode === 'reset') {
      const result = await api('/auth/reset', {method: 'POST', body: {...resetBody, mfa_code: code}});
      resetBody = null; resetToken = null; $('mfa-form').reset(); $('mfa-form').hidden = true;
      message.textContent = result.message; $('auth-links').hidden = false;
    } else {
      await api('/auth/mfa/verify', {method: 'POST', body: {code}});
      $('mfa-form').reset(); location.assign('/overview');
    }
  } catch (error) { errorMessage(error); $('mfa-form').elements.code.select(); }
  finally { setBusy(); }
});
$('mfa-use-recovery').addEventListener('click', () => {
  usingRecovery = !usingRecovery;
  const input = $('mfa-form').elements.code;
  input.value = ''; input.inputMode = usingRecovery ? 'text' : 'numeric';
  input.maxLength = usingRecovery ? 64 : 6; input.minLength = usingRecovery ? 32 : 6;
  input.pattern = usingRecovery ? '[A-Fa-f0-9\\-]{32,64}' : '[0-9]{6}';
  input.placeholder = usingRecovery ? 'Saved recovery code' : '000000';
  $('mfa-label').textContent = usingRecovery ? 'Recovery code' : 'Authenticator code';
  $('mfa-use-recovery').textContent = usingRecovery ? 'Use an authenticator code' : 'Use a recovery code';
  $('auth-description').textContent = usingRecovery ? 'Each recovery code works once.' : 'Enter the code from your authenticator.';
  input.focus();
});
$('mfa-cancel').addEventListener('click', async () => {
  if (busy) return;
  setBusy('mfa');
  try { await api('/auth/logout', {method: 'POST'}); resetBody = null; form.reset(); $('mfa-form').reset(); location.assign('/login'); }
  catch (error) { errorMessage(error); setBusy(); }
});

// Bind submission guards before waiting for session or wallet configuration.
try {
  const session = await boot();
  if (session.user && ['login', 'register'].includes(view)) location.replace('/overview');
  else {
    sessionReady = true; setBusy();
    if (session.mfa_required && view !== 'reset') showMfa();
    if (['login', 'register'].includes(view) && !mfaMode) try {
      const config = await api('/config');
      wallet = new Wallet(config);
      wallet.addEventListener('status', event => { if (!mfaMode) { message.textContent = event.detail; message.classList.remove('is-error'); } });
    } catch (error) { $('siwe-login').title = error.message; }
    setBusy(busy);
  }
} catch (error) { errorMessage(error); setBusy(); }

// Credentials stay only in the active form or pending reset request, never storage.
window.addEventListener('pagehide', () => { form.reset(); $('mfa-form').reset(); resetBody = null; });
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

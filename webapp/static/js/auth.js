import {api, boot, $, element} from './api.js?v=2';
import {Wallet, walletError} from './wallet.js?v=5';

const view = location.pathname.slice(1), form = $('auth-form'), message = $('auth-message');
let resetToken = null, wallet = null, sessionReady = false, busy = null;
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

function setBusy(method = null) {
  busy = method;
  $('auth-submit').disabled = !sessionReady || !!busy || (view === 'reset' && !resetToken);
  $('siwe-login').disabled = !sessionReady || !!busy || !wallet;
  $('auth-submit').setAttribute('aria-busy', String(busy === 'email'));
  $('siwe-login').setAttribute('aria-busy', String(busy === 'wallet'));
  $('auth-submit').textContent = busy === 'email' ? pendingLabel : submitLabel;
  $('wallet-login-label').textContent = busy === 'wallet' ? 'Waiting for wallet…' : 'Continue with wallet';
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!sessionReady || busy || (view === 'reset' && !resetToken)) return;
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
    } else await api('/auth/login', {method: 'POST', body: {email: data.email, password: data.password}});
    form.reset(); location.assign('/overview');
  } catch (error) { errorMessage(error); }
  finally { setBusy(); }
});
$('show-password').addEventListener('click', () => {
  const showing = form.elements.password.type === 'password';
  form.elements.password.type = showing ? 'text' : 'password';
  $('show-password').setAttribute('aria-label', showing ? 'Hide password' : 'Show password');
  $('show-password').setAttribute('aria-pressed', String(showing));
});
$('siwe-login').addEventListener('click', async () => {
  if (!sessionReady || busy || !wallet) return;
  setBusy('wallet'); message.classList.remove('is-error'); message.textContent = '';
  try { await wallet.connect(); message.textContent = 'Confirm the sign-in message in your wallet.'; await wallet.authenticate('login'); location.assign('/overview'); }
  catch (error) { if (error.code !== 'WALLET_SELECTION_CANCELLED') errorMessage(error); else message.textContent = ''; }
  finally { setBusy(); }
});

// Bind submission guards before waiting for session or wallet configuration.
try {
  const session = await boot();
  if (session.user && ['login', 'register'].includes(view)) location.replace('/overview');
  else {
    sessionReady = true; setBusy();
    try {
      const config = await api('/config');
      wallet = new Wallet(config);
      wallet.addEventListener('status', event => { message.textContent = event.detail; message.classList.remove('is-error'); });
      document.querySelector('.environment').lastChild.textContent = config.chain_id === 31337 ? 'Anvil' : 'Sepolia';
    } catch (error) { $('siwe-login').title = error.message; }
    setBusy(busy);
  }
} catch (error) { errorMessage(error); setBusy(); }

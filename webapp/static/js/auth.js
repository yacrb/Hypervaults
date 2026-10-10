import {api, boot, $, element} from './api.js?v=2';
import {Wallet, walletError} from './wallet.js?v=4';

const view = location.pathname.slice(1), form = $('auth-form'), message = $('auth-message');
let resetToken = null, wallet = null;
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
$('auth-submit').textContent = {login: 'Sign in', register: 'Create account', recover: 'Send recovery link', reset: 'Reset password'}[view] || 'Sign in';
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

try {
  const session = await boot();
  if (session.user && ['login', 'register'].includes(view)) location.replace('/overview');
  try {
    const config = await api('/config');
    wallet = new Wallet(config);
    wallet.addEventListener('status', event => { message.textContent = event.detail; message.classList.remove('is-error'); });
    $('siwe-login').disabled = false;
    document.querySelector('.environment').lastChild.textContent = config.chain_id === 31337 ? 'Local testnet' : 'Sepolia testnet';
  } catch (error) { $('siwe-login').disabled = true; $('siwe-login').title = error.message; }
} catch (error) { errorMessage(error); $('auth-submit').disabled = true; $('siwe-login').disabled = true; }

form.addEventListener('submit', async event => {
  event.preventDefault(); message.textContent = ''; message.classList.remove('is-error');
  const data = Object.fromEntries(new FormData(form));
  if (['register', 'reset'].includes(view) && data.password !== data.confirm) { errorMessage(new Error('Passwords do not match.')); return; }
  $('auth-submit').disabled = true;
  $('auth-submit').setAttribute('aria-busy', 'true');
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
  finally { $('auth-submit').disabled = view === 'reset' && !resetToken; $('auth-submit').setAttribute('aria-busy', 'false'); }
});
$('show-password').addEventListener('click', () => {
  const showing = form.elements.password.type === 'password';
  form.elements.password.type = showing ? 'text' : 'password';
  $('show-password').setAttribute('aria-label', showing ? 'Hide password' : 'Show password');
  $('show-password').setAttribute('aria-pressed', String(showing));
});
$('siwe-login').addEventListener('click', async () => {
  $('siwe-login').disabled = true; $('siwe-login').setAttribute('aria-busy', 'true'); message.classList.remove('is-error'); message.textContent = '';
  try { await wallet.connect(); message.textContent = 'Confirm the sign-in message in your wallet.'; await wallet.authenticate('login'); location.assign('/overview'); }
  catch (error) { if (error.code !== 'WALLET_SELECTION_CANCELLED') errorMessage(error); else message.textContent = ''; }
  finally { $('siwe-login').disabled = !wallet; $('siwe-login').setAttribute('aria-busy', 'false'); }
});

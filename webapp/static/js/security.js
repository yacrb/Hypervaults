// Account confirmation dialogs share one cancellation and pending-state boundary.
import {api, element} from './api.js?v=2';
import {walletError} from './wallet.js?v=5';

function field(form, name, label, type = 'text') {
  const wrapper = element('label', '', label), input = element('input');
  input.name = name; input.type = type; input.required = true;
  input.maxLength = name === 'code' ? 64 : 128;
  input.autocomplete = name === 'password' ? 'current-password' : 'one-time-code';
  wrapper.append(input); form.append(wrapper); return input;
}

function confirmation(title, build, submit) {
  return new Promise((resolve, reject) => {
    const dialog = element('dialog', 'confirmation-dialog'), form = element('form', 'form-stack');
    form.method = 'post';
    const heading = element('h2', '', title); heading.id = 'confirmation-title';
    dialog.setAttribute('aria-labelledby', heading.id); form.append(heading); build(form);
    const controls = element('div', 'form-actions'), save = element('button', 'button', 'Confirm');
    save.type = 'submit';
    const cancel = element('button', 'button button-secondary', 'Cancel'); cancel.type = 'button';
    controls.append(save, cancel);
    const message = element('p', 'form-message is-error'); message.setAttribute('role', 'alert');
    form.append(controls, message); dialog.append(form); document.body.append(dialog);
    let busy = false, completed = false;
    cancel.addEventListener('click', () => dialog.close());
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('close', () => {
      form.reset(); dialog.remove();
      if (!completed) reject(Object.assign(new Error('Confirmation cancelled.'), {code: 'AUTH_CANCELLED'}));
    }, {once: true});
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy) return;
      busy = true; save.disabled = cancel.disabled = true; save.setAttribute('aria-busy', 'true'); message.textContent = '';
      try {
        const result = await submit(Object.fromEntries(new FormData(form)));
        completed = true; dialog.close(); resolve(result);
      } catch (error) { message.textContent = walletError(error); }
      finally { busy = false; save.disabled = cancel.disabled = false; save.setAttribute('aria-busy', 'false'); }
    });
    dialog.showModal();
  });
}

export function confirmFactor(user, submit, title = 'Two-factor verification') {
  if (!user.mfa_enabled) return submit('');
  return confirmation(title, form => {
    field(form, 'code', 'Authenticator or recovery code');
    form.append(element('p', 'form-hint muted', 'Use a fresh code. Each code works once.'));
  }, data => submit(data.code.trim()));
}

export function reauthenticate(user, wallet, mfaAction = null) {
  const title = mfaAction === 'disable' ? 'Turn off two-factor authentication' : mfaAction === 'recovery-codes' ? 'Replace recovery codes' : 'Verify your identity';
  return confirmation(title, form => {
    if (user.has_password) field(form, 'password', 'Current password', 'password');
    else form.append(element('p', 'form-hint muted', 'Confirm an ownership message with your linked wallet.'));
    if (user.mfa_enabled) field(form, 'code', 'Authenticator or recovery code');
  }, async data => {
    if (user.has_password) return api('/auth/reauth', {method: 'POST', body: {password: data.password, mfa_code: data.code?.trim() || '', ...(mfaAction ? {mfa_action: mfaAction} : {})}});
    if (!wallet) throw new Error('Wallet configuration unavailable. Try again shortly.');
    if (!wallet.address) await wallet.connect();
    if (!user.wallets.includes(wallet.address)) throw new Error('Connect a wallet linked to this account.');
    return wallet.authenticate('reauth', data.code?.trim() || '', mfaAction);
  });
}

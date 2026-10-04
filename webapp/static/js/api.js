let csrf = null;

export class ApiError extends Error {
  constructor(message, status, state) { super(message); this.status = status; this.state = state; }
}

export async function api(path, {method = 'GET', body, timeout = 20000} = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = {Accept: 'application/json'};
  if (method !== 'GET') {
    if (csrf) headers['X-CSRF-Token'] = csrf;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
  }
  try {
    const response = await fetch(`/api${path}`, {
      method, headers, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({detail: 'The server returned an unreadable response.'}));
    if (!response.ok) throw new ApiError(data.detail || 'Request failed.', response.status, data.state);
    if (data.csrf) csrf = data.csrf;
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new ApiError('The request timed out. Data could not be refreshed.', 503, 'unavailable');
    if (error instanceof ApiError) throw error;
    throw new ApiError('The application is unreachable. Try again shortly.', 503, 'unavailable');
  } finally { clearTimeout(timer); }
}

export function boot() { return api('/session'); }
export const $ = id => document.getElementById(id);
export function element(tag, className = '', content = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== '') node.textContent = String(content);
  return node;
}
export function empty(target, message) { target.replaceChildren(element('p', 'empty', message)); }
export function icon(name) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.classList.add('icon'); node.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `/static/icons.svg#${name}`); node.append(use);
  return node;
}
export function emptyState(target, title, message, href = null, label = '') {
  const state = element('div', 'empty-state'), mark = element('span', 'empty-symbol');
  mark.append(icon('vault')); state.append(mark, element('strong', '', title), element('p', '', message));
  if (href) { const link = element('a', 'text-link', label); link.href = href; state.append(link); }
  target.replaceChildren(state);
}
export function disclosure(label, content) {
  const node = element('details', 'compact-details');
  node.append(element('summary', '', label), content); return node;
}
export function button(text, callback, secondary = false) {
  const node = element('button', `button button-small${secondary ? ' button-secondary' : ''}`, text);
  node.type = 'button';
  node.addEventListener('click', callback);
  return node;
}
export function detail(label, value) {
  const row = element('div', 'detail-row');
  row.append(element('dt', '', label), element('dd', '', value));
  return row;
}
export function shortAddress(address) { return address ? `${address.slice(0, 6)}…${address.slice(-4)}` : 'Disconnected'; }
export function dateTime(seconds) {
  return new Date(Number(seconds) * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
}
export function dateString(iso) { return new Date(iso).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}); }

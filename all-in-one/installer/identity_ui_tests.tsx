import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { DEV_PROFILE_STORAGE_KEY, GH_USER_PARAM } from '../dev-profile';

vi.mock('@michelangelo-ai/rpc', () => ({
  request: vi.fn((name: string) => name === 'ListProject'
    ? Promise.resolve({ projectList: { items: [] } })
    : Promise.reject(new Error(`Unexpected query: ${name}`))),
  normalizeTranscoderError: vi.fn(() => null),
}));

function verifiedProfile(overrides = {}) {
  return { id: 'member-1', domain_id: 'domain-1', subject: 'subject-1',
    name: 'Verified Operator', email: 'operator@example.test', enabled: true,
    role: 'operator', ...overrides };
}
function identityResponse(profile = verifiedProfile(), contentType = 'application/json', ok = true) {
  const fetch = vi.fn().mockResolvedValue({ ok,
    headers: new Headers({ 'Content-Type': contentType }),
    json: async () => profile });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

it('uses the verified IAM member and ignores developer identity overrides', async () => {
  window.history.replaceState(null, '', `/?${new URLSearchParams({ [GH_USER_PARAM]: 'ada' })}`);
  window.localStorage.setItem(DEV_PROFILE_STORAGE_KEY, JSON.stringify({ name: 'Ada Lovelace' }));
  const fetch = identityResponse();
  render(<App />);
  expect((await screen.findAllByRole('button', { name: /Verified Operator/, hidden: true })).length).toBeGreaterThan(0);
  expect(fetch).toHaveBeenCalledWith('/api/identity/me', {
    credentials: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Users', hidden: true })).not.toBeInTheDocument();
});

it('shows directory administration for verified administrators', async () => {
  identityResponse(verifiedProfile({ role: 'admin' }));
  render(<App />);
  expect((await screen.findAllByRole('link', { name: 'Users', hidden: true })).length).toBeGreaterThan(0);
});

it.each([
  { enabled: false }, { subject: '' }, { domain_id: '' }, { role: 'unknown' },
])('rejects an invalid membership profile: %j', async overrides => {
  identityResponse(verifiedProfile(overrides));
  render(<App />);
  expect(await screen.findByText('Your application identity could not be verified.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Verified Operator/, hidden: true })).not.toBeInTheDocument();
});

it('rejects an HTML login response instead of showing an authenticated shell', async () => {
  identityResponse(verifiedProfile(), 'text/html');
  render(<App />);
  expect(await screen.findByText('The application identity service did not return a verified profile.')).toBeInTheDocument();
});

it('rejects a denied membership response', async () => {
  identityResponse(verifiedProfile(), 'application/json', false);
  render(<App />);
  expect(await screen.findByText('Your application access could not be verified.')).toBeInTheDocument();
});

it('signs out through the OCI session endpoint', async () => {
  identityResponse();
  const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {});
  render(<App />);
  await userEvent.click((await screen.findAllByRole('button', { name: /Verified Operator/, hidden: true }))[0]);
  await userEvent.click(screen.getByRole('option', { name: 'Sign out' }));
  expect(assign).toHaveBeenCalledWith('/oauth2/sign_out');
  assign.mockRestore();
});

#!/usr/bin/env python3
"""Apply the per-user Studio shell to the assembled, verified source.

The reviewed release inventory is immutable. This overlay is separate source
included in the installer checksum and applied before the normal UI build.
"""
from pathlib import Path

APP = r'''import { useEffect, useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom-v5-compat';
import { CoreApp, TimeZone, UserRole } from '@michelangelo-ai/core';
import { normalizeTranscoderError, request } from '@michelangelo-ai/rpc';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Client as Styletron } from 'styletron-engine-atomic';
import { Provider as StyletronProvider } from 'styletron-react';
import { ICONS } from './icons/icons';
import { IdentityUsersPage, IdentityProfile } from './IdentityUsersPage';

const engine = new Styletron();
const queryClient = new QueryClient();
const TEAM = { id: 'michelangelo', displayName: 'Michelangelo', url: '/' };
function resolveTeams(uuids: string[]) {
  return Promise.resolve(Object.fromEntries(uuids.map(uuid => [uuid, TEAM])));
}

export function App() {
  const [user, setUser] = useState<IdentityProfile | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    fetch('/api/identity/me', { credentials: 'same-origin', cache: 'no-store', redirect: 'error' }).then(async membership => {
      if (!membership.ok) throw new Error('Your application access could not be verified.');
      if (!membership.headers.get('Content-Type')?.includes('application/json')) {
        throw new Error('The application identity service did not return a verified profile.');
      }
      const profile = await membership.json() as IdentityProfile;
      if (!profile.enabled || !profile.id || !profile.subject || !profile.domain_id || !profile.email ||
          !['admin', 'operator'].includes(profile.role)) {
        throw new Error('Your application identity could not be verified.');
      }
      if (current) setUser({ ...profile, name: profile.name || profile.email });
    }).catch((reason: Error) => {
      if (current) setError(reason instanceof TypeError ? 'Your session could not be verified. Sign out and sign in again.' : reason.message);
    });
    return () => { current = false; };
  }, []);
  if (!user) return <main style={{ padding: 40, fontFamily: 'system-ui' }}>
    <h1>Michelangelo</h1><p>{error || 'Verifying your OCI IAM identity…'}</p>
    {error && <><button onClick={() => window.location.reload()}>Retry</button>{' '}
      <a href="/oauth2/sign_out">Sign out</a></>}
  </main>;
  const links = [{ label: 'Docs', href: 'https://michelangelo-ai.github.io/michelangelo/' }];
  if (user.role === 'admin') links.unshift({ label: 'Users', href: '/users' });
  const dependencies = {
    error: { normalizeError: normalizeTranscoderError }, theme: { icons: ICONS },
    service: { request, resolvers: { team: resolveTeams } },
    navigationBar: { links, onSignOut: async () => { window.location.assign('/oauth2/sign_out'); } },
    // Both application roles are tenant ML operators. Directory administration
    // is separately enforced by the identity service; no project RBAC is implied.
    user: { name: user.name, email: user.email, role: UserRole.Admin, timeZone: TimeZone.Local },
  };
  return <StyletronProvider value={engine}><QueryClientProvider client={queryClient}>
    <BrowserRouter><Routes>
      <Route path="/users" element={<IdentityUsersPage actor={user} />} />
      <Route path="/*" element={<CoreApp dependencies={dependencies} />} />
    </Routes></BrowserRouter>
  </QueryClientProvider></StyletronProvider>;
}
'''

USERS = r'''import { CSSProperties, FormEvent, useCallback, useEffect, useState } from 'react';

export interface IdentityProfile {
  id: string; domain_id: string; subject: string | null; email: string; name: string;
  role: 'admin' | 'operator'; enabled: boolean;
}
const panel: CSSProperties = { padding: 24, border: '1px solid #dbe2ea', borderRadius: 12, background: '#fff', marginTop: 24 };
const button: CSSProperties = { padding: '9px 14px', border: '1px solid #cbd5e1', borderRadius: 6, background: '#fff', cursor: 'pointer' };
const field: CSSProperties = { padding: 10, border: '1px solid #cbd5e1', borderRadius: 6 };
async function api(path: string, method = 'GET', payload?: object) {
  const response = await fetch('/api/identity/users' + path, {
    method, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: method === 'GET' ? {} : { 'Content-Type': 'application/json', 'X-MA-CSRF': '1' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}

export function IdentityUsersPage({ actor }: { actor: IdentityProfile }) {
  const [users, setUsers] = useState<IdentityProfile[]>([]);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'operator' | 'admin'>('operator');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => { setUsers((await api('')).users); }, []);
  useEffect(() => { if (actor.role === 'admin') refresh().catch((e: Error) => setError(e.message)); }, [actor.role, refresh]);
  async function change(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); await refresh(); setNotice(message); }
    catch (e) { setError(e instanceof Error ? e.message : 'The request failed.'); }
    finally { setBusy(false); }
  }
  async function add(event: FormEvent) {
    event.preventDefault();
    await change(async () => { await api('', 'POST', { email, role }); setEmail(''); },
      'Invitation saved. This person can now sign in with their existing OCI IAM account.');
  }
  if (actor.role !== 'admin') return <main style={{ padding: 40, fontFamily: 'system-ui' }}>
    <h1>Application administrators only</h1><p>You can use the ML platform as an operator.</p><a href="/">Return to Studio</a>
  </main>;
  return <main style={{ background: '#f7f9fc', minHeight: '100vh', color: '#172033', fontFamily: 'system-ui', padding: '32px max(24px, calc((100vw - 1120px) / 2))' }}>
    <nav style={{ display: 'flex', justifyContent: 'space-between', gap: 20 }}>
      <a href="/">← Michelangelo Studio</a><span>{actor.name} · {actor.email} · <a href="/oauth2/sign_out">Sign out</a></span>
    </nav>
    <h1 style={{ marginBottom: 8 }}>Users</h1>
    <p style={{ color: '#526077' }}>Manage access for existing OCI IAM identities. Changes take effect on the next application request.</p>
    <p style={{ color: '#526077' }}>Operators can create and run ML workloads. Administrators also manage application users. These roles apply across this deployment.</p>
    {error && <p role="alert" style={{ ...panel, borderColor: '#dd6666', color: '#a51c30' }}>{error}</p>}
    {notice && <p role="status" style={{ ...panel, borderColor: '#68a889', color: '#17613f' }}>{notice}</p>}
    <section style={panel}>
      <h2 style={{ marginTop: 0 }}>Invite an existing IAM user</h2>
      <p>Enter the verified email on their account in this deployment’s OCI identity domain. This invitation does not create an IAM account.</p>
      <form onSubmit={add} style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <label>Email <input aria-label="IAM email" type="email" value={email} onChange={e => setEmail(e.target.value)} required maxLength={320} style={{ ...field, minWidth: 280 }} /></label>
        <label>Role <select aria-label="Invitation role" value={role} onChange={e => setRole(e.target.value as 'operator' | 'admin')} style={field}>
          <option value="operator">Operator</option><option value="admin">Administrator</option>
        </select></label>
        <button type="submit" disabled={busy} style={{ ...button, background: '#2557d6', color: '#fff' }}>Add user</button>
      </form>
    </section>
    <section style={panel}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><h2>Application members</h2>
        <button disabled={busy} style={button} onClick={() => change(refresh, 'Membership refreshed.')}>Refresh</button></div>
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
        <thead><tr>{['Identity', 'Role', 'Status', 'Access'].map(x => <th key={x} style={{ padding: '12px 8px', borderBottom: '2px solid #dbe2ea' }}>{x}</th>)}</tr></thead>
        <tbody>{users.map(user => <tr key={user.id}>
          <td style={{ padding: '16px 8px', borderBottom: '1px solid #e8edf3' }}><strong>{user.name || user.email}</strong><br /><span style={{ color: '#526077' }}>{user.email}</span>{user.id === actor.id && <small> · You</small>}</td>
          <td style={{ padding: 8 }}><select aria-label={'Role for ' + user.email} value={user.role} disabled={busy || user.id === actor.id}
            onChange={e => { const value = e.target.value; void change(() => api('/' + user.id, 'PATCH', { role: value }), 'Role updated.'); }} style={field}>
            <option value="operator">Operator</option><option value="admin">Administrator</option></select></td>
          <td style={{ padding: 8 }}>{!user.enabled ? 'Access removed' : user.subject ? 'Active' : 'Invited · first sign-in pending'}</td>
          <td style={{ padding: 8 }}><button style={button} disabled={busy || user.id === actor.id} onClick={() => {
            if (user.enabled && !window.confirm('Remove application access for ' + user.email + '? Existing sessions will be denied on their next request.')) return;
            void change(() => api('/' + user.id, 'PATCH', { enabled: !user.enabled }), user.enabled ? 'Application access removed.' : 'Application access restored.');
          }}>{user.enabled ? 'Remove access' : 'Restore access'}</button></td>
        </tr>)}</tbody>
      </table></div>
      <p style={{ color: '#526077', fontSize: 14 }}>Your own administrator access and the last active administrator are protected. Removing application access does not delete an OCI IAM account.</p>
    </section>
  </main>;
}
'''


def prepare_source(source):
    source = Path(source)
    app = source / "javascript/app"
    if not (app / "App.tsx").exists():
        raise RuntimeError("Verified Studio source was not found for identity overlay")
    (app / "App.tsx").write_text(APP)
    (app / "IdentityUsersPage.tsx").write_text(USERS)
    # Replace the inherited shared-operator tests with the IAM shell contract.
    test_path = app / "__tests__/app.test.tsx"
    test_path.parent.mkdir(exist_ok=True)
    test_path.write_text(Path(__file__).with_name("identity_ui_tests.tsx").read_text())
    test_path.chmod(0o644)
    template = source / "helm/michelangelo/templates/core/ui-configmap.yaml"
    text = template.read_text()
    start = text.index("location /api/ {")
    end = text.index("\n    }", start)
    block = text[start:end]
    if 'proxy_set_header Authorization "";' in block:
        block = block.replace('proxy_set_header Authorization "";', 'proxy_set_header Authorization $http_authorization;')
    elif "proxy_set_header Authorization $http_authorization;" not in block:
        raise RuntimeError("Unexpected API proxy authorization template")
    template.write_text(text[:start] + block + text[end:])
    text = template.read_text()
    start = text.index("location /pilot-logs/ {")
    end = text.index("\n    }", start)
    block = text[start:end].replace("proxy_set_header Authorization $http_authorization;", 'proxy_set_header Authorization "";')
    template.write_text(text[:start] + block + text[end:])
    for path in (app / "App.tsx", app / "IdentityUsersPage.tsx", template):
        path.chmod(0o644)

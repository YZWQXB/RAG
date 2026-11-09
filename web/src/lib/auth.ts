const TOKEN_KEY = "rag_token";
const TENANT_KEY = "rag_tenant";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(TENANT_KEY);
}

export type TenantInfo = { id: string; slug: string; name: string; role: string };

export function setTenant(tenant: TenantInfo): void {
  localStorage.setItem(TENANT_KEY, JSON.stringify(tenant));
}

export function getTenant(): TenantInfo | null {
  const raw = localStorage.getItem(TENANT_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as TenantInfo;
  } catch {
    return null;
  }
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/Alert";
import { ApiError, api } from "../lib/api";
import { getAuthClaims, getTenant } from "../lib/auth";
import type { Kb } from "./KbList";

type Member = {
  user_id: string;
  email: string;
  name: string;
  role: string;
};

type AclEntry = {
  id: string;
  subject_type: "user" | "role";
  subject_id: string;
  permission: "read" | "write" | "manage";
};

const VALID_ROLES = ["admin", "editor", "viewer"] as const;
const PERMISSIONS = ["read", "write", "manage"] as const;

const ROLE_LABELS: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  viewer: "只读成员",
};

const PERMISSION_LABELS: Record<string, string> = {
  read: "可读",
  write: "可写",
  manage: "可管理",
};

function memberLabel(acl: AclEntry, members: Member[]): string {
  if (acl.subject_type === "role") return "角色：" + (ROLE_LABELS[acl.subject_id] ?? acl.subject_id);
  const member = members.find((m) => m.user_id === acl.subject_id);
  return member ? member.email : "用户 " + acl.subject_id.slice(0, 8) + "…（已移除）";
}

export function Settings() {
  const tenant = getTenant();
  const claims = getAuthClaims();
  const isManager = tenant?.role === "owner" || tenant?.role === "admin";

  const [members, setMembers] = useState<Member[] | null>(null);
  const [kbs, setKbs] = useState<Kb[] | null>(null);
  const [acls, setAcls] = useState<Record<string, AclEntry[]>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // 邀请成员表单
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<(typeof VALID_ROLES)[number]>("viewer");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState("");

  // 授权表单（每个库共用一套输入状态，kbId 记录当前在给哪个库加）
  const [aclKbId, setAclKbId] = useState<string | null>(null);
  const [aclSubjectType, setAclSubjectType] = useState<"user" | "role">("user");
  const [aclSubjectId, setAclSubjectId] = useState("");
  const [aclPermission, setAclPermission] = useState<(typeof PERMISSIONS)[number]>("read");
  const [aclSubmitting, setAclSubmitting] = useState(false);
  const [aclError, setAclError] = useState("");

  const load = async () => {
    try {
      const [memberList, kbList] = await Promise.all([
        api.get<Member[]>("/api/v1/members"),
        api.get<Kb[]>("/api/v1/kbs"),
      ]);
      setMembers(memberList);
      setKbs(kbList);
      const entries = await Promise.all(
        kbList.map(async (kb) => [kb.id, await api.get<AclEntry[]>(`/api/v1/kbs/${kb.id}/acl`)] as const),
      );
      setAcls(Object.fromEntries(entries));
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载失败，请稍后重试");
      setMembers([]);
      setKbs([]);
    }
  };

  useEffect(() => {
    if (isManager) void load();
  }, [isManager]);

  if (!isManager) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-8">
        <Alert tone="warning">只有空间所有者或管理员可以管理成员与授权。</Alert>
        <Link to="/kbs" className="text-sm text-blue-700 underline">
          返回知识库
        </Link>
      </div>
    );
  }

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    setInviteError("");
    if (!inviteEmail.trim()) {
      setInviteError("请输入成员邮箱");
      return;
    }
    setInviting(true);
    try {
      await api.post<Member>("/api/v1/members", {
        email: inviteEmail.trim(),
        role: inviteRole,
      });
      setNotice(`已把 ${inviteEmail.trim()} 以「${ROLE_LABELS[inviteRole]}」身份加入空间`);
      setInviteEmail("");
      await load();
    } catch (err) {
      setInviteError(err instanceof ApiError ? err.message : "添加失败，请稍后重试");
    } finally {
      setInviting(false);
    }
  };

  const changeRole = async (userId: string, role: string) => {
    setError("");
    try {
      await api.patch(`/api/v1/members/${userId}`, { role });
      setNotice("角色已更新，该成员下次请求即按新角色放行");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "修改失败");
    }
  };

  const removeMember = async (member: Member) => {
    if (!window.confirm(`确定移除 ${member.email}？TA 对本空间所有知识库的授权将同时被清理。`)) return;
    setError("");
    try {
      await api.del(`/api/v1/members/${member.user_id}`);
      setNotice(`已移除 ${member.email}，TA 的现有令牌立即失效`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "移除失败");
    }
  };

  const openAclForm = (kbId: string) => {
    setAclKbId(kbId);
    setAclSubjectType("user");
    setAclSubjectId("");
    setAclPermission("read");
    setAclError("");
  };

  const submitAcl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!aclKbId) return;
    setAclError("");
    const value = aclSubjectId.trim();
    if (!value) {
      setAclError(aclSubjectType === "user" ? "请输入成员邮箱或用户 ID" : "请选择角色");
      return;
    }
    setAclSubmitting(true);
    try {
      let subjectId = value;
      if (aclSubjectType === "user") {
        // 界面上输入邮箱：先在成员列表里换算成 user_id（授权接口只收 ID）
        const member = (members ?? []).find(
          (m) => m.email === value.toLowerCase() || m.user_id === value,
        );
        if (!member) {
          setAclError("该邮箱不在本空间，请先在上方添加成员");
          return;
        }
        subjectId = member.user_id;
      }
      await api.put(`/api/v1/kbs/${aclKbId}/acl`, {
        subject_type: aclSubjectType,
        subject_id: subjectId,
        permission: aclPermission,
      });
      setNotice("授权已保存，对方刷新页面即可看到该知识库");
      setAclKbId(null);
      await load();
    } catch (err) {
      setAclError(err instanceof ApiError ? err.message : "授权失败，请稍后重试");
    } finally {
      setAclSubmitting(false);
    }
  };

  const revokeAcl = async (kbId: string, acl: AclEntry) => {
    if (!window.confirm(`确定撤销「${memberLabel(acl, members ?? [])}」的${PERMISSION_LABELS[acl.permission]}权限？`)) return;
    setError("");
    try {
      await api.del(`/api/v1/kbs/${kbId}/acl/${acl.id}`);
      setNotice("授权已撤销，对方刷新后该知识库立即不可见");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "撤销失败");
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-base font-medium text-zinc-900">成员与授权</h1>
          <p className="mt-1 text-xs text-zinc-500">
            当前空间：{tenant?.name ?? "—"} · 你是{ROLE_LABELS[tenant?.role ?? ""] ?? tenant?.role}
          </p>
        </div>
        <Link to="/kbs" className="text-sm text-zinc-500 underline">
          返回知识库
        </Link>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      {members === null || kbs === null ? (
        <div className="h-40 animate-pulse rounded-lg border border-zinc-200 bg-zinc-100" />
      ) : (
        <div className="space-y-8">
          {/* ============ 成员管理 ============ */}
          <section className="rounded-lg border border-zinc-200 bg-white p-4">
            <h2 className="mb-3 text-sm font-medium text-zinc-900">成员（{members.length}）</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-200 text-left text-xs text-zinc-500">
                  <th className="py-2">邮箱</th>
                  <th className="py-2">角色</th>
                  <th className="py-2 w-40">操作</th>
                </tr>
              </thead>
              <tbody>
                {members.map((m) => {
                  const isSelf = m.user_id === claims?.sub;
                  const isOwner = m.role === "owner";
                  return (
                    <tr key={m.user_id} className="border-b border-zinc-100">
                      <td className="py-2 text-zinc-800">
                        {m.email}
                        {isSelf ? <span className="ml-2 text-xs text-zinc-400">（你）</span> : null}
                      </td>
                      <td className="py-2">
                        {isOwner || isSelf ? (
                          <span className="text-zinc-700">{ROLE_LABELS[m.role] ?? m.role}</span>
                        ) : (
                          <select
                            value={m.role}
                            onChange={(e) => void changeRole(m.user_id, e.target.value)}
                            className="rounded-md border border-zinc-300 px-2 py-1 text-xs"
                          >
                            {VALID_ROLES.map((r) => (
                              <option key={r} value={r}>
                                {ROLE_LABELS[r]}
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td className="py-2">
                        {isOwner || isSelf ? (
                          <span className="text-xs text-zinc-400">
                            {isOwner ? "不可移除" : "不能移除自己"}
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => void removeMember(m)}
                            className="text-xs text-red-600 underline"
                          >
                            移除
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            <form onSubmit={invite} className="mt-4 border-t border-zinc-100 pt-4">
              <div className="flex items-end gap-2">
              <div className="flex-1">
                <label className="mb-1 block text-sm text-zinc-700">邀请成员（输入已注册邮箱）</label>
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="colleague@example.com"
                  className={`w-full rounded-md border px-3 py-2 text-sm outline-none transition ${
                    inviteError ? "border-red-600" : "border-zinc-300 focus:border-blue-600"
                  }`}
                />
                {inviteError ? <p className="mt-1 text-xs text-red-700">{inviteError}</p> : null}
              </div>
              <div className="w-32">
                <label className="mb-1 block text-sm text-zinc-700">角色</label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as (typeof VALID_ROLES)[number])}
                  className="w-full rounded-md border border-zinc-300 px-2 py-2 text-sm"
                >
                  {VALID_ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABELS[r]}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="submit"
                disabled={inviting}
                className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {inviting ? "添加中…" : "添加"}
              </button>
              </div>
            </form>
            <p className="mt-1 text-xs text-zinc-500">
              对方需已注册（任意空间均可）；viewer 只读、editor 可上传文档、admin 可帮你管理成员与授权。
            </p>
          </section>

          {/* ============ 知识库授权 ============ */}
          <section className="rounded-lg border border-zinc-200 bg-white p-4">
            <h2 className="mb-1 text-sm font-medium text-zinc-900">知识库授权</h2>
            <p className="mb-3 text-xs text-zinc-500">
              owner / admin 天然可见全部知识库，无需授权；这里的授权面向 editor / viewer。
            </p>
            {kbs.length === 0 ? (
              <p className="text-sm text-zinc-500">还没有知识库。</p>
            ) : (
              <div className="space-y-4">
                {kbs.map((kb) => (
                  <div key={kb.id} className="rounded-md border border-zinc-200 p-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-medium text-zinc-800">{kb.name}</h3>
                      <button
                        type="button"
                        onClick={() => (aclKbId === kb.id ? setAclKbId(null) : openAclForm(kb.id))}
                        className="text-xs text-blue-700 underline"
                      >
                        {aclKbId === kb.id ? "收起" : "添加授权"}
                      </button>
                    </div>

                    {(acls[kb.id] ?? []).length === 0 ? (
                      <p className="mt-2 text-xs text-zinc-500">暂无针对 editor / viewer 的授权。</p>
                    ) : (
                      <ul className="mt-2 space-y-1">
                        {(acls[kb.id] ?? []).map((acl) => (
                          <li
                            key={acl.id}
                            className="flex items-center justify-between rounded bg-zinc-50 px-2 py-1 text-xs"
                          >
                            <span className="text-zinc-700">
                              {memberLabel(acl, members ?? [])} ·{" "}
                              {PERMISSION_LABELS[acl.permission]}
                            </span>
                            <button
                              type="button"
                              onClick={() => void revokeAcl(kb.id, acl)}
                              className="text-red-600 underline"
                            >
                              撤销
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}

                    {aclKbId === kb.id ? (
                      <form onSubmit={submitAcl} className="mt-3 rounded border border-zinc-200 bg-zinc-50 p-3">
                        <div className="flex items-end gap-2">
                          <div className="w-36">
                            <label className="mb-1 block text-xs text-zinc-700">授权对象</label>
                            <select
                              value={aclSubjectType}
                              onChange={(e) => setAclSubjectType(e.target.value as "user" | "role")}
                              className="w-full rounded-md border border-zinc-300 px-2 py-2 text-sm"
                            >
                              <option value="user">指定成员</option>
                              <option value="role">整类角色</option>
                            </select>
                          </div>
                          <div className="flex-1">
                            {aclSubjectType === "user" ? (
                              <div>
                                <label className="mb-1 block text-xs text-zinc-700">成员邮箱（须已加入本空间）</label>
                                <input
                                  type="email"
                                  value={aclSubjectId}
                                  onChange={(e) => setAclSubjectId(e.target.value)}
                                  placeholder="colleague@example.com"
                                  className={`w-full rounded-md border px-2 py-2 text-sm outline-none transition ${
                                    aclError ? "border-red-600" : "border-zinc-300 focus:border-blue-600"
                                  }`}
                                />
                              </div>
                            ) : (
                              <div>
                                <label className="mb-1 block text-xs text-zinc-700">角色</label>
                                <select
                                  value={aclSubjectId}
                                  onChange={(e) => setAclSubjectId(e.target.value)}
                                  className="w-full rounded-md border border-zinc-300 px-2 py-2 text-sm"
                                >
                                  <option value="">请选择…</option>
                                  {VALID_ROLES.map((r) => (
                                    <option key={r} value={r}>
                                      {ROLE_LABELS[r]}
                                    </option>
                                  ))}
                                </select>
                              </div>
                            )}
                          </div>
                          <div className="w-28">
                            <label className="mb-1 block text-xs text-zinc-700">权限</label>
                            <select
                              value={aclPermission}
                              onChange={(e) => setAclPermission(e.target.value as (typeof PERMISSIONS)[number])}
                              className="w-full rounded-md border border-zinc-300 px-2 py-2 text-sm"
                            >
                              {PERMISSIONS.map((p) => (
                                <option key={p} value={p}>
                                  {PERMISSION_LABELS[p]}
                                </option>
                              ))}
                            </select>
                          </div>
                          <button
                            type="submit"
                            disabled={aclSubmitting}
                            className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                          >
                            {aclSubmitting ? "保存中…" : "授权"}
                          </button>
                        </div>
                        {aclPermission === "manage" ? (
                          <p className="mt-2 text-xs text-amber-700">
                            ⚠️ 可管理包含 <b>删除知识库</b> 与修改授权，请确认对方可信。
                          </p>
                        ) : null}
                        {aclError ? <p className="mt-2 text-xs text-red-700">{aclError}</p> : null}
                      </form>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

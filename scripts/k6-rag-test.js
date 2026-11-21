import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const fullLatency = new Trend("chat_full_duration", true);
const chatOk = new Rate("chat_success_rate");

export const options = {
  stages: [
    { duration: "30s", target: 5 },
    { duration: "40s", target: 10 },
    { duration: "40s", target: 20 },
    { duration: "30s", target: 0 },
  ],
  thresholds: {
    "http_req_duration{name:list_kbs}": ["p(95)<500"],
    "chat_success_rate": ["rate>0.7"],
  },
};

const BASE = "http://localhost:8080";
// 每账号：自己空间的 token + 自己空间的库（不跨租户）
const ACCOUNTS = [
  { email: "alice@test.com", slug: "alice-space", kb: "d5854a8a-01f4-4e6b-a2ba-ecaa491dc777" },
  { email: "bob@test.com", slug: "bob-space", kb: "4c6bf9e6-0723-4c0e-8e67-323a62abccea" },
  { email: "carol@test.com", slug: "carol-space", kb: "84f9b2a4-8917-4e6d-8668-99be278ad096" },
  { email: "dave@test.com", slug: "dave-space", kb: "95ff44c1-55af-4517-a619-169f45d5c8e0" },
  { email: "eve@test.com", slug: "eve-space", kb: "95ff44c1-55af-4517-a619-169f45d5c8e0" },
];

function loginAccount(email, slug) {
  const d = http.post(BASE + "/api/v1/auth/login",
    JSON.stringify({ email: email, password: "Passw0rd123" }),
    { headers: { "Content-Type": "application/json" } }).json();
  if (d.selection_required) {
    const tid = d.tenants.find(t => t.slug === slug).id;
    const tr = http.post(BASE + "/api/v1/auth/token",
      JSON.stringify({ email: email, password: "Passw0rd123", tenant_id: tid }),
      { headers: { "Content-Type": "application/json" } });
    return tr.json().token;
  }
  return d.token;
}

export function setup() {
  const tokens = {};
  for (const acc of ACCOUNTS) { tokens[acc.email] = loginAccount(acc.email, acc.slug); }
  return { tokens: tokens };
}

export default function (data) {
  const acc = ACCOUNTS[(__VU - 1) % ACCOUNTS.length];
  const params = { headers: { Authorization: "Bearer " + data.tokens[acc.email] } };
  const lr = http.get(BASE + "/api/v1/kbs",
    Object.assign({ tags: { name: "list_kbs" } }, params));
  check(lr, { "list 200": (res) => res.status === 200 });
  if (__ITER % 4 === 0) {
    const started = Date.now();
    const payload = JSON.stringify({ kb_ids: [acc.kb], query: "介绍一下" });
    const cr = http.post(BASE + "/api/v1/chat/stream", payload,
      Object.assign({ tags: { name: "chat_stream" } }, params));
    fullLatency.add(Date.now() - started);
    // fake LLM 下 done 或 no_answer 都算链路成功
    const ok = cr.status === 200 && (cr.body.indexOf("done") !== -1 || cr.body.indexOf("no_answer") !== -1);
    chatOk.add(ok);
    check(cr, { "chat 200": (res) => res.status === 200 });
  }
  sleep(2);
}
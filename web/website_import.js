import { api } from "../../scripts/api.js";

let websiteSession;
const tiers = { MONTHLY: "月度会员", QUARTERLY: "季度会员", ANNUAL: "年度会员", LIFETIME: "终身赞助" };

function make(tag, text = "", options = {}) {
    const element = document.createElement(tag);
    element.textContent = text;
    Object.assign(element, options);
    return element;
}

async function websiteRequest(path, body) {
    const response = await api.fetchApi("/personal-lora/site/" + path, body === undefined ? {} : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw Object.assign(new Error(result.message ?? "无法访问官网导入服务"), { code: result.code });
    }
    return result;
}

export function openWebsiteImport(onImport, showMembership = false) {
    const dialog = make("dialog", "", { className: "personal-lora-dialog personal-lora-website" });
    dialog.setAttribute("aria-label", "从黑曜官网下载 LoRA");
    const form = make("form", "", { method: "dialog" });
    const heading = make("strong", "官网 LoRA 导入");
    const searchLine = make("div", "", { className: "personal-lora-website-line" });
    const query = make("input", "", { placeholder: "搜索角色，或粘贴黑曜官网 LoRA 详情链接" });
    query.setAttribute("aria-label", "角色名或官网详情链接");
    const search = make("button", "搜索", { type: "button" });
    searchLine.append(query, search);

    const role = make("select", "", { hidden: true });
    role.setAttribute("aria-label", "选择官网角色 LoRA");
    const mode = make("select", "", { hidden: true });
    mode.setAttribute("aria-label", "官网触发词档位");
    mode.append(new Option("简短触发词", "short"), new Option("完整头部特征触发词", "head"));
    const prompt = make("textarea", "", { readOnly: true, hidden: true });
    prompt.setAttribute("aria-label", "官网触发提示词预览");
    const quota = make("p", "", { className: "personal-lora-quota", hidden: true });
    quota.setAttribute("aria-live", "polite");
    const status = make("output");
    status.setAttribute("aria-live", "polite");

    const account = make("div", "", { className: "personal-lora-website-line", hidden: true });
    const username = make("input", "", { placeholder: "黑曜官网账号", autocomplete: "username" });
    const password = make("input", "", { type: "password", placeholder: "官网密码", autocomplete: "current-password" });
    username.setAttribute("aria-label", "黑曜官网账号");
    password.setAttribute("aria-label", "官网密码");
    const login = make("button", "登录官网", { type: "button" });
    account.append(username, password, login);

    const controls = make("nav");
    const importButton = make("button", "导入到新槽位", { type: "button", disabled: true });
    const logout = make("button", "退出官网登录", { type: "button", hidden: !websiteSession });
    const close = make("button", "关闭");
    controls.append(importButton, logout, close);
    form.append(heading, searchLine, role, mode, prompt, account, quota, status, controls);
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    dialog.addEventListener("close", () => dialog.remove(), { once: true });

    let activeDetail;
    let busy = false;
    let renewTicket = false;
    async function refreshQuota() {
        const session = showMembership ? websiteSession : undefined;
        quota.hidden = !session;
        if (!session) return;
        try {
            const state = await websiteRequest("membership", { session });
            if (websiteSession !== session) return;
            const tier = state.active ? (tiers[state.tier] ?? "官网会员") : "未开通下载会员";
            const remaining = state.unlimited ? "不限量" : "剩余 " + state.remaining;
            quota.textContent = tier + " · " + state.month + " LoRA · 已用 " + state.used + " · " + remaining;
        } catch (error) {
            if (websiteSession === session) quota.textContent = error.message;
        }
    }

    void refreshQuota();
    async function run(action) {
        if (busy) return;
        busy = true;
        for (const control of [search, role, importButton, login, logout]) control.disabled = true;
        try {
            await action();
        } catch (error) {
            status.textContent = error.message;
            if (error.code === "LOGIN_REQUIRED") {
                websiteSession = undefined;
                logout.hidden = true;
                account.hidden = false;
                username.focus();
            }
            if (error.code === "NEW_TICKET_REQUIRED") {
                renewTicket = true;
                importButton.textContent = "重新申请官网票据";
            }
            await refreshQuota();
        } finally {
            busy = false;
            for (const control of [search, role, login, logout]) control.disabled = false;
            importButton.disabled = !activeDetail?.available;
        }
    }

    mode.onchange = () => {
        prompt.value = (mode.value === "head" ? activeDetail?.head_prompt : activeDetail?.prompt) ?? "";
    };

    async function showRole() {
        activeDetail = undefined;
        renewTicket = false;
        prompt.hidden = true;
        mode.hidden = true;
        account.hidden = true;
        if (!role.value) return;
        status.textContent = "正在核对官网版本和本地权重…";
        activeDetail = await websiteRequest("role/" + encodeURIComponent(role.value));
        mode.value = "short";
        mode.hidden = !activeDetail.head_prompt || activeDetail.head_prompt === activeDetail.prompt;
        prompt.value = activeDetail.prompt;
        prompt.hidden = !activeDetail.prompt;
        importButton.textContent = activeDetail.local ? "导入到新槽位" : "下载并导入";
        const size = (activeDetail.bytes / 1024 ** 2).toFixed(1) + " MB";
        const triggerNote = activeDetail.prompt ? "" : " · 无固定触发词";
        if (activeDetail.local) {
            status.textContent = "已校验本地同版权重 · " + activeDetail.version + " · " + size + triggerNote;
        } else {
            status.textContent = activeDetail.available
                ? "需使用官网账号的下载权益 · " + activeDetail.version + " · " + size + triggerNote
                : "官网目前没有提供这个权重的直连";
        }
    }

    search.onclick = () => run(async () => {
        activeDetail = undefined;
        renewTicket = false;
        prompt.hidden = true;
        mode.hidden = true;
        account.hidden = true;
        role.hidden = true;
        status.textContent = "正在搜索官网 LoRA…";
        const { items } = await websiteRequest("search?" + new URLSearchParams({ q: query.value.trim() }));
        role.replaceChildren(new Option("选择角色和版本", ""));
        role.append(...items.map((item) => new Option(item.title, item.id)));
        role.hidden = !items.length;
        status.textContent = items.length ? "选择要导入的角色版本。" : "没有找到对应 LoRA。";
        if (items.length === 1) {
            role.selectedIndex = 1;
            await showRole();
        }
    });
    query.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            search.click();
        }
    });
    role.onchange = () => run(showRole);
    login.onclick = () => run(async () => {
        status.textContent = "正在登录黑曜官网…";
        try {
            const result = await websiteRequest("login", { username: username.value.trim(), password: password.value });
            websiteSession = result.session;
            account.hidden = true;
            logout.hidden = false;
            status.textContent = "已登录官网，可以下载并导入。";
            await refreshQuota();
        } finally {
            password.value = "";
        }
    });
    logout.onclick = () => run(async () => {
        await websiteRequest("logout", { session: websiteSession });
        websiteSession = undefined;
        logout.hidden = true;
        status.textContent = "已退出官网登录。";
        await refreshQuota();
    });
    importButton.onclick = () => run(async () => {
        const replace = renewTicket;
        renewTicket = false;
        importButton.textContent = activeDetail.local ? "导入到新槽位" : "下载并导入";
        status.textContent = activeDetail.local ? "正在导入已校验的同版权重…" : "正在下载并校验权重…";
        const result = await websiteRequest("import", {
            id: role.value, session: websiteSession, download: !activeDetail.local, renewTicket: replace,
        });
        if (dialog.open) {
            onImport({ ...result, prompt_mode: mode.value });
            dialog.close();
        }
    });
}

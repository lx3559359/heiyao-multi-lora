import { api } from "../../scripts/api.js";

let websiteSession;
const tiers = { MONTHLY: "月度会员", QUARTERLY: "季度会员", ANNUAL: "年度会员", LIFETIME: "终身赞助" };

export function make(tag, text = "", options = {}) {
    const element = document.createElement(tag);
    element.textContent = text;
    Object.assign(element, options);
    return element;
}

export async function websiteRequest(path, body, signal) {
    const response = await api.fetchApi("/personal-lora/site/" + path, body === undefined ? { signal } : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw Object.assign(new Error(result.message ?? "无法访问官网导入服务"), { code: result.code });
    }
    return result;
}

const downloadPhases = {
    prepare: "正在准备下载",
    zip_prepare: "正在等待官网打包",
    file: "正在下载权重",
    zip: "正在下载官网 ZIP",
    verify: "正在校验权重",
    extract: "正在解压和校验权重",
};

function sizeText(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + " KiB";
    return (bytes / 1024 ** 2).toFixed(1) + " MiB";
}

export async function websiteDownload(path, body, container, title) {
    if (!body.download || !globalThis.crypto?.randomUUID) return websiteRequest(path, body);
    const progressId = globalThis.crypto.randomUUID();
    const row = make("div", "", { className: "personal-lora-download-row" });
    const description = make("small", title + " · " + downloadPhases.prepare);
    const bar = make("progress", "", { max: 100 });
    bar.setAttribute("aria-label", title + " 下载进度");
    row.append(description, bar);
    container.append(row);
    container.hidden = false;

    const polling = new AbortController();
    let timer = setTimeout(poll, 250);
    async function poll() {
        try {
            const { phase, received, total } = await websiteRequest("progress/" + progressId, undefined, polling.signal);
            if (polling.signal.aborted) return;
            let message = title + " · " + (downloadPhases[phase] ?? downloadPhases.prepare);
            if (total > 0) {
                const percent = Math.min(100, Math.floor(received / total * 100));
                bar.value = percent;
                message += " · " + sizeText(received) + " / " + sizeText(total) + " · " + percent + "%";
            } else {
                bar.removeAttribute("value");
                if (phase === "zip") message += " · 已下载 " + sizeText(received);
            }
            description.textContent = message;
        } catch {
            // A progress read can race the start or end of the actual download request.
        } finally {
            if (!polling.signal.aborted) timer = setTimeout(poll, 450);
        }
    }

    try {
        return await websiteRequest(path, { ...body, progressId });
    } finally {
        clearTimeout(timer);
        polling.abort();
        row.remove();
        container.hidden = !container.childElementCount;
    }
}

export function websiteAccount(showMembership = true) {
    const quota = make("p", "", { className: "personal-lora-quota", hidden: true });
    quota.setAttribute("aria-live", "polite");
    const account = make("div", "", { className: "personal-lora-website-line", hidden: true });
    const username = make("input", "", { placeholder: "黑曜官网账号", autocomplete: "username" });
    const password = make("input", "", { type: "password", placeholder: "官网密码", autocomplete: "current-password" });
    username.setAttribute("aria-label", "黑曜官网账号");
    password.setAttribute("aria-label", "官网密码");
    const login = make("button", "登录官网", { type: "button" });
    account.append(username, password, login);

    const logout = make("button", "退出官网登录", { type: "button", hidden: !websiteSession });

    function requireLogin() {
        websiteSession = undefined;
        logout.hidden = true;
        account.hidden = false;
        username.focus();
    }

    async function signIn() {
        try {
            const result = await websiteRequest("login", { username: username.value.trim(), password: password.value });
            websiteSession = result.session;
            account.hidden = true;
            logout.hidden = false;
        } finally {
            password.value = "";
        }
    }

    async function signOut() {
        await websiteRequest("logout", { session: websiteSession });
        websiteSession = undefined;
        logout.hidden = true;
    }

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

    return { account, quota, login, logout, requireLogin, signIn, signOut, refreshQuota,
        get session() { return websiteSession; } };
}

export function openWebsiteImport(onImport, showMembership = false, update = null) {
    const updating = Boolean(update);
    const dialog = make("dialog", "", { className: "personal-lora-dialog personal-lora-website" });
    const title = updating ? "检查官网新版" : "官网 LoRA 导入";
    dialog.setAttribute("aria-label", title);
    const form = make("form", "", { method: "dialog" });
    const heading = make("strong", title);
    const searchLine = make("div", "", { className: "personal-lora-website-line" });
    const query = make("input", "", { placeholder: "搜索角色或风格，或粘贴官网 LoRA 链接" });
    query.setAttribute("aria-label", "角色名、风格名或官网 LoRA 链接");
    const search = make("button", updating ? "重新检查" : "搜索", { type: "button" });
    if (!updating) searchLine.append(query);
    searchLine.append(search);
    const localVersion = make("div", "", { hidden: !updating });
    const remoteVersion = make("div", "", { hidden: !updating });

    const role = make("select", "", { hidden: true });
    role.setAttribute("aria-label", "选择官网 LoRA");
    const mode = make("select", "", { hidden: true });
    mode.setAttribute("aria-label", "官网触发词档位");
    mode.append(new Option("简短触发词", "short"), new Option("完整头部特征触发词", "head"));
    const prompt = make("textarea", "", { readOnly: true, hidden: true });
    prompt.setAttribute("aria-label", "官网触发提示词预览");
    const auth = websiteAccount(showMembership);
    const { account, quota, login, logout, refreshQuota } = auth;
    const status = make("output");
    status.setAttribute("aria-live", "polite");

    const controls = make("nav");
    const importButton = make("button", updating ? "下载并切换当前槽位" : "导入到新槽位",
        { type: "button", disabled: true });
    const downloadButton = make("button", "只下载", { type: "button", disabled: true, hidden: !updating });
    const close = make("button", "关闭");
    controls.append(downloadButton, importButton, logout, close);
    const progress = make("div", "", { className: "personal-lora-download-progress", hidden: true });
    form.append(heading, searchLine, localVersion, remoteVersion, role, mode, prompt, account, quota, status, progress, controls);
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    dialog.addEventListener("close", () => dialog.remove(), { once: true });

    let activeDetail;
    let busy = false;
    let renewTicket = false;
    void refreshQuota();
    async function run(action) {
        if (busy) return;
        busy = true;
        for (const control of [search, role, importButton, downloadButton, login, logout]) control.disabled = true;
        try {
            await action();
        } catch (error) {
            status.textContent = error.message;
            if (error.code === "LOGIN_REQUIRED") {
                auth.requireLogin();
            }
            if (error.code === "NEW_TICKET_REQUIRED") {
                renewTicket = true;
                importButton.textContent = "重新申请官网票据";
                downloadButton.textContent = "重新申请并只下载";
            }
            await refreshQuota();
        } finally {
            busy = false;
            for (const control of [search, role, login, logout]) control.disabled = false;
            importButton.disabled = !activeDetail?.available;
            downloadButton.disabled = !activeDetail?.available || activeDetail.local;
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

    async function checkVersion() {
        activeDetail = undefined;
        renewTicket = false;
        prompt.hidden = true;
        mode.hidden = true;
        account.hidden = true;
        status.textContent = "正在核对当前权重和官网版本…";
        localVersion.textContent = "当前文件 · " + update.name;
        remoteVersion.textContent = "";
        const result = await websiteRequest("update?" + new URLSearchParams({ name: update.name }));
        localVersion.textContent = "本地版本 · " + (result.current.version ?? "未识别") + " · " + update.name;
        remoteVersion.textContent = "官网版本 · " + (result.latest?.version ?? "未匹配到同款") +
            (result.latest?.title ? " · " + result.latest.title : "");
        if (result.state !== "update") {
            status.textContent = ({
                current: "已是官网版本。",
                equivalent: "官网权重与本地文件一致。",
                missing: "ComfyUI 安装目录下缺少当前权重。",
                unknown: "当前权重暂无可核验的官网登记。",
                unlisted: "官网目录里暂无同款的已发布版本。",
                ambiguous: "官网有多个匹配版本，需先核对型号与权重登记。",
                unverified: "已发现新版，暂无可核验的权重登记。",
            })[result.state] ?? "暂无可用更新。";
            return;
        }
        activeDetail = result.latest;
        importButton.textContent = activeDetail.local ? "切换当前槽位" : "下载并切换当前槽位";
        downloadButton.textContent = "只下载";
        mode.value = "short";
        mode.hidden = !activeDetail.head_prompt || activeDetail.head_prompt === activeDetail.prompt;
        prompt.value = activeDetail.prompt ?? "";
        prompt.hidden = !activeDetail.prompt;
        const size = (activeDetail.bytes / 1024 ** 2).toFixed(1) + " MB";
        status.textContent = activeDetail.local ? "新版已在本机 · " + size
            : activeDetail.available ? "新版可下载 · " + size + " · 使用官网账号下载额度"
            : "新版暂无官网直连";
    }

    async function searchCatalog() {
        activeDetail = undefined;
        renewTicket = false;
        prompt.hidden = true;
        mode.hidden = true;
        account.hidden = true;
        role.hidden = true;
        status.textContent = "正在搜索官网 LoRA…";
        const { items } = await websiteRequest("search?" + new URLSearchParams({ q: query.value.trim() }));
        role.replaceChildren(new Option("选择 LoRA 和版本", ""));
        role.append(...items.map((item) => new Option(item.title, item.id)));
        role.hidden = !items.length;
        status.textContent = items.length ? `找到 ${items.length} 条 LoRA，请选择要导入的版本。` : "没有找到对应 LoRA。";
        if (items.length === 1) {
            role.selectedIndex = 1;
            await showRole();
        }
    }
    search.onclick = () => run(updating ? checkVersion : searchCatalog);
    query.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            search.click();
        }
    });
    role.onchange = () => run(showRole);
    login.onclick = () => run(async () => {
        status.textContent = "正在登录黑曜官网…";
        await auth.signIn();
        status.textContent = "已登录官网，可以下载。";
        await refreshQuota();
    });
    logout.onclick = () => run(async () => {
        await auth.signOut();
        status.textContent = "已退出官网登录。";
        await refreshQuota();
    });
    async function downloadVersion(switchCurrent) {
        const replace = renewTicket;
        renewTicket = false;
        status.textContent = activeDetail.local ? "正在校验新版权重…" : "正在下载并校验新版权重…";
        const result = await websiteDownload("update", {
            name: update.name, id: activeDetail.id, session: websiteSession,
            download: !activeDetail.local, renewTicket: replace, switch: switchCurrent,
        }, progress, activeDetail.title);
        if (!dialog.open) return;
        onImport(result, switchCurrent);
        if (switchCurrent) {
            dialog.close();
        } else {
            activeDetail.local = true;
            importButton.textContent = "切换当前槽位";
            status.textContent = "新版已下载 · " + result.name;
            await refreshQuota();
        }
    }
    downloadButton.onclick = () => run(() => downloadVersion(false));
    importButton.onclick = () => run(async () => {
        if (updating) {
            await downloadVersion(true);
            return;
        }
        const replace = renewTicket;
        renewTicket = false;
        importButton.textContent = activeDetail.local ? "导入到新槽位" : "下载并导入";
        status.textContent = activeDetail.local ? "正在导入已校验的同版权重…" : "正在下载并校验权重…";
        const result = await websiteDownload("import", {
            id: role.value, session: websiteSession, download: !activeDetail.local, renewTicket: replace,
        }, progress, activeDetail.title);
        if (dialog.open) {
            onImport({ ...result, prompt_mode: mode.value });
            dialog.close();
        }
    });
    if (updating) search.click();
}

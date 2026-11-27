import { make, websiteAccount, websiteDownload, websiteRequest } from "./website_import.js";

export function openWebsiteLibrary(onDownload) {
    const title = "官网 LoRA 库比对";
    const dialog = make("dialog", "", { className: "personal-lora-dialog personal-lora-website personal-lora-library" });
    dialog.setAttribute("aria-label", title);
    const form = make("form", "", { method: "dialog" });
    const totals = make("p", "", { className: "personal-lora-library-totals", hidden: true });
    totals.setAttribute("aria-live", "polite");
    const filter = make("input", "", { type: "search", placeholder: "筛选缺少的角色或风格" });
    filter.setAttribute("aria-label", "筛选缺少的 LoRA");
    const scanButton = make("button", "重新比对", { type: "button" });
    const searchLine = make("div", "", { className: "personal-lora-website-line" });
    searchLine.append(filter, scanButton);
    const selectAll = make("input", "", { type: "checkbox" });
    const selectLabel = make("label", "", { className: "personal-lora-library-line", hidden: true });
    selectLabel.append(selectAll, make("span", "全选当前筛选结果"));
    const list = make("ul", "", { className: "personal-lora-library-list", hidden: true });
    list.setAttribute("aria-label", "缺少的官网 LoRA");
    const pending = make("details", "", { hidden: true });
    const pendingSummary = make("summary");
    const pendingList = make("ul", "", { className: "personal-lora-library-list" });
    pending.append(pendingSummary, pendingList);

    const auth = websiteAccount();
    const { account, quota, login, logout } = auth;
    const status = make("output");
    status.setAttribute("aria-live", "polite");
    const controls = make("nav");
    const download = make("button", "下载所选", { type: "button", disabled: true });
    const stop = make("button", "停止后续下载", { type: "button", hidden: true });
    const close = make("button", "关闭", { type: "submit" });
    controls.append(download, stop, logout, close);
    const progress = make("div", "", { className: "personal-lora-download-progress", hidden: true });
    form.append(make("strong", title), totals, searchLine, selectLabel, list, pending, account, quota, status, progress, controls);
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    dialog.addEventListener("close", () => dialog.remove(), { once: true });

    let result;
    let visible = [];
    let busy = false;
    let downloading = false;
    let stopRequested = false;
    const selected = new Set();
    const errors = new Map();
    const renewals = new Map();
    const statusCodes = new Set(["LOGIN_REQUIRED", "DOWNLOAD_QUOTA_EXHAUSTED", "DOWNLOAD_MEMBERSHIP_REQUIRED"]);

    function syncControls() {
        download.textContent = selected.size ? "下载所选（" + selected.size + "）" : "下载所选";
        download.disabled = busy || !selected.size;
        scanButton.disabled = filter.disabled = busy;
        close.disabled = downloading;
        login.disabled = logout.disabled = busy;
        selectAll.disabled = busy || !visible.length;
        const visibleSelected = visible.filter(({ id }) => selected.has(id)).length;
        selectAll.checked = visible.length > 0 && visibleSelected === visible.length;
        selectAll.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
        for (const input of list.querySelectorAll("input")) {
            input.disabled = busy;
            if (!input.value.endsWith(":renew")) input.checked = selected.has(input.value);
        }
        stop.hidden = !downloading;
        stop.disabled = stopRequested;
    }

    function render() {
        if (!result) return;
        const { local_count, website_count, matched_count, missing, unverified } = result;
        totals.hidden = false;
        totals.textContent = "本地 " + local_count + " · 官网 " + website_count +
            " · 已有官网同版 " + matched_count + " · 缺少 " + missing.length +
            (unverified.length ? " · 待核对 " + unverified.length : "");
        pending.hidden = !unverified.length;
        pendingSummary.textContent = "查看 " + unverified.length + " 条权重登记待核对的 LoRA";
        pendingList.replaceChildren(...unverified.map(({ title }) => make("li", title)));
        const term = filter.value.trim().toLocaleLowerCase();
        visible = missing.filter(({ title }) => title.toLocaleLowerCase().includes(term));
        list.hidden = !missing.length;
        selectLabel.hidden = !missing.length;
        const scroll = list.scrollTop;
        const rows = visible.map((item) => {
            const { id, title, bytes } = item;
            const row = make("li");
            const label = make("label", "", { className: "personal-lora-library-line" });
            const input = make("input", "", { type: "checkbox", value: id });
            input.onchange = () => {
                if (input.checked) selected.add(id);
                else selected.delete(id);
                syncControls();
            };
            label.append(input, make("span", title), make("small", (bytes / 1024 ** 2).toFixed(1) + " MB"));
            row.append(label);
            if (errors.has(id)) row.append(make("small", errors.get(id), { className: "personal-lora-library-error" }));
            if (renewals.has(id)) {
                const renewLabel = make("label", "", { className: "personal-lora-library-error" });
                const renew = make("input", "", { type: "checkbox", value: id + ":renew", checked: renewals.get(id) });
                renew.onchange = () => renewals.set(id, renew.checked);
                renewLabel.append(renew, make("span", "已核对额度，允许重新申请此项官网票据"));
                row.append(renewLabel);
            }
            return row;
        });
        list.replaceChildren(...(visible.length || !missing.length ? rows : [make("li", "当前筛选没有匹配项。")]));
        list.scrollTop = scroll;
        syncControls();
    }

    async function run(operation) {
        if (busy) return;
        busy = true;
        syncControls();
        try {
            await operation();
        } catch (error) {
            status.textContent = error.message;
            if (error.code === "LOGIN_REQUIRED") auth.requireLogin();
        } finally {
            busy = downloading = stopRequested = false;
            syncControls();
        }
    }

    async function scan() {
        status.textContent = "正在比对官网目录和本地权重，首次扫描需读取文件校验值…";
        const fresh = await websiteRequest("library");
        const missingIds = new Set(fresh.missing.map(({ id }) => id));
        for (const id of selected) if (!missingIds.has(id)) selected.delete(id);
        for (const id of errors.keys()) if (!renewals.has(id) || !missingIds.has(id)) errors.delete(id);
        for (const id of renewals.keys()) if (!missingIds.has(id)) renewals.delete(id);
        result = fresh;
        render();
        status.textContent = fresh.missing.length ? "勾选要下载的 LoRA，通过官网账号核销下载额度。"
            : "已核对的官网 LoRA 均在本地。";
    }

    filter.oninput = render;
    filter.addEventListener("keydown", (event) => {
        if (event.key === "Enter") event.preventDefault();
    });
    selectAll.onchange = () => {
        for (const { id } of visible) {
            if (selectAll.checked) selected.add(id);
            else selected.delete(id);
        }
        syncControls();
    };
    scanButton.onclick = () => run(scan);
    login.onclick = () => run(async () => {
        await auth.signIn();
        status.textContent = "已登录官网，可下载勾选的 LoRA。";
        await auth.refreshQuota();
    });
    logout.onclick = () => run(async () => {
        await auth.signOut();
        status.textContent = "已退出官网登录。";
        await auth.refreshQuota();
    });
    stop.onclick = () => {
        stopRequested = true;
        status.textContent = "已开始的下载处理结束后停止。";
        syncControls();
    };
    dialog.addEventListener("cancel", (event) => {
        if (downloading) {
            event.preventDefault();
            stop.click();
        }
    });
    form.addEventListener("submit", (event) => {
        if (event.submitter !== close || downloading) event.preventDefault();
    });
    download.onclick = () => run(async () => {
        if (!auth.session) {
            auth.requireLogin();
            status.textContent = "请先登录官网，勾选的 LoRA 已保留。";
            return;
        }
        downloading = true;
        syncControls();
        const queue = result.missing.filter(({ id }) => selected.has(id));
        const session = auth.session;
        const attempted = new Set();
        const active = new Set();
        let completed = 0;
        let reason = "";
        let failed = 0;

        async function downloadNext() {
            while (!stopRequested && !reason) {
                const item = queue.find(({ id, sha256 }) => selected.has(id) && !attempted.has(id) && !active.has(sha256));
                if (!item) return;
                attempted.add(item.id);
                active.add(item.sha256);
                status.textContent = "已完成 " + completed + " 项 · 正在处理所选 LoRA";
                errors.delete(item.id);
                try {
                    const renewTicket = renewals.get(item.id) === true;
                    if (renewTicket) renewals.set(item.id, false);
                    const receipt = await websiteDownload("library/download", {
                        id: item.id, sha256: item.sha256, session, download: true, renewTicket,
                    }, progress, item.title);
                    const available = result.missing.filter(({ sha256 }) => sha256 === item.sha256);
                    result.matched_count += available.length;
                    if (!receipt.reused) result.local_count++;
                    result.missing = result.missing.filter(({ sha256 }) => sha256 !== item.sha256);
                    for (const { id } of available) {
                        selected.delete(id);
                        errors.delete(id);
                        renewals.delete(id);
                    }
                    completed++;
                } catch (error) {
                    failed++;
                    errors.set(item.id, error.message);
                    if (error.code === "NEW_TICKET_REQUIRED") renewals.set(item.id, false);
                    if (statusCodes.has(error.code)) {
                        reason = error.message;
                        if (error.code === "LOGIN_REQUIRED") auth.requireLogin();
                    }
                } finally {
                    active.delete(item.sha256);
                    render();
                }
            }
        }

        await Promise.all([downloadNext(), downloadNext()]);
        if (completed) onDownload();
        render();
        const earlier = Math.max(0, errors.size - failed);
        status.textContent = "本次完成 " + completed + " 项 · 失败 " + failed + " 项" +
            (earlier ? " · 此前失败待重试 " + earlier + " 项" : "") +
            (stopRequested ? " · 已停止后续下载" : "") + (reason ? " · " + reason : "");
        await auth.refreshQuota();
    });
    void auth.refreshQuota();
    void run(scan);
}

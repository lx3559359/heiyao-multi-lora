import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { openWebsiteImport } from "./website_import.js";
import { switchSlotWeight } from "./slot_version.js";
import { openWebsiteLibrary } from "./website_library.js";

const blankRow = (inject) => ({
    name: "", enabled: false, strength: 0.9, inject, overrides: {},
    prompt_mode: "short", head_overrides: {},
});

function parseRows(value) {
    const saved = JSON.parse(value);
    if (!Array.isArray(saved) || saved.some((row) => !row || typeof row !== "object" || Array.isArray(row))) {
        throw new Error("槽位必须是 JSON 对象数组");
    }
    return saved.map((row, index) => ({ ...blankRow(index === 1), ...row }));
}

const changes = new EventTarget();
const style = document.createElement("link");
style.rel = "stylesheet";
style.href = new URL("./personal_lora.css", import.meta.url).href;
document.head.append(style);


function element(tag, title, content = "") {
    const item = document.createElement(tag);
    if (title) {
        item.title = title;
        item.setAttribute("aria-label", title);
    }
    item.textContent = content;
    return item;
}

async function request(path, method = "GET", body) {
    const options = { method };
    if (body !== undefined) {
        options.headers = { "Content-Type": "application/json" };
        options.body = JSON.stringify(body);
    }
    const response = await api.fetchApi("/personal-lora/" + path, options);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
}

function importPrompt(source, mode) {
    const lines = source.replace(/^\uFEFF/, "").split(/\r?\n|[\u2028\u2029]/);
    const shortHeader = /^\s*(?:简短触发词|完整触发提示词)[^：:]*[：:]\s*/;
    const headHeader = /^\s*完整头部特征触发词[^：:]*[：:]\s*/;
    const preferred = mode === "head" ? headHeader : shortHeader;
    let header = preferred;
    let section = lines.findIndex((line) => header.test(line));
    if (section === -1 && mode === "head") {
        header = shortHeader;
        section = lines.findIndex((line) => header.test(line));
    }
    if (section === -1) return source.trim();
    const content = [lines[section].replace(header, "")];
    for (const line of lines.slice(section + 1)) {
        if (/^\s*[\u3400-\u9fff][^：:]{0,30}[：:]/.test(line)) break;
        if (!line.trim() && content.some((part) => part.trim())) break;
        content.push(line);
    }
    return content.join("\n").trim();
}

async function editTrigger(row, refresh, changed) {
    const { defaults, head_defaults: heads = {} } = await refresh();
    const name = row.name;
    const mode = row.prompt_mode;
    const head = mode === "head";
    const overrides = head ? row.head_overrides : row.overrides;
    const label = head ? "头部特征触发词" : "简短触发词";
    const dialog = element("dialog", "编辑 LoRA 触发提示词");
    dialog.className = "personal-lora-dialog";
    const form = element("form");
    form.method = "dialog";
    const heading = element("strong", "", label + " · " + name);
    const field = element("textarea", label);
    field.value = overrides[name] ?? (head ? heads[name] : undefined) ?? defaults[name] ?? "";
    const buttons = element("nav");
    const message = element("output");
    const file = element("input", "选择 TXT");
    file.type = "file";
    file.accept = ".txt,text/plain";
    file.hidden = true;
    file.onchange = async () => {
        try {
            if (file.files[0]) field.value = importPrompt(await file.files[0].text(), mode);
        } catch (error) {
            message.textContent = error.message;
        }
        file.value = "";
    };

    const button = (label, action) => {
        const control = element("button", "", label);
        control.type = "button";
        control.onclick = action;
        return control;
    };
    buttons.append(
        button("导入 TXT", () => file.click()),
        button("仅保存到工作流", () => {
            overrides[name] = field.value.trim();
            changed();
            dialog.close();
        }),
        button("恢复本档默认值", () => {
            delete overrides[name];
            changed();
            dialog.close();
        }),
    );
    const save = button("保存为 LoRA 默认", async () => {
        save.disabled = true;
        try {
            await request("default", "PUT", { name, prompt: field.value, mode });
            delete overrides[name];
            changed();
            changes.dispatchEvent(new Event("catalog"));
            dialog.close();
        } catch (error) {
            message.textContent = error.message;
        } finally {
            save.disabled = false;
        }
    });
    buttons.append(save, button("取消", () => dialog.close()));
    form.append(heading, field, file, buttons, message);
    dialog.append(form);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    document.body.append(dialog);
    dialog.showModal();
}

function addSlots(node, name, inputData) {
    const advertised = inputData?.[1]?.default;
    const initial = advertised && advertised !== "[]" ? advertised : "[{}, {}]";
    let rows = parseRows(initial);
    let catalog = { loras: [], defaults: {} };
    let timer;
    let previewVersion = 0;
    let disposed = false;
    const panel = element("section", "个人多 LoRA 槽位");
    panel.className = "personal-lora";

    const toolbar = element("header");
    toolbar.className = "personal-lora-toolbar";
    const brand = element("div");
    brand.className = "personal-lora-brand";
    const brandSite = element("a", "黑曜资源官网", "openheiyao.top ↗");
    brandSite.href = "https://openheiyao.top/";
    brandSite.target = "_blank";
    brandSite.rel = "noopener noreferrer";
    brand.append(element("strong", "", "由黑曜资源提供"), brandSite);
    const add = element("button", "新增 LoRA 槽位", "＋ 新增 LoRA");
    add.type = "button";
    add.className = "personal-lora-add";
    const actions = element("div");
    actions.className = "personal-lora-header-actions";
    const website = element("button", "从黑曜官网导入 LoRA", "官网导入");
    website.type = "button";
    website.hidden = true;
    const library = element("button", "比对黑曜官网 LoRA 库", "官网比对");
    library.type = "button";
    library.hidden = true;
    actions.append(library, website, add);
    toolbar.append(brand, actions);

    const cards = element("div");
    cards.className = "personal-lora-cards";
    const message = element("output");
    message.setAttribute("aria-live", "polite");
    const details = element("details");
    const preview = element("pre", "拼接后的正向提示词");
    details.append(element("summary", "", "正向提示词预览"), preview);
    panel.append(toolbar, cards, message, details);

    const sceneWidget = () => node.widgets?.find((widget) => widget.name === "scene_prompt");

    const updatePreview = () => {
        clearTimeout(timer);
        const version = ++previewVersion;
        if (!details.open || disposed) return;
        timer = setTimeout(async () => {
            const linked = node.inputs?.some((input) => input.name === "scene_prompt" && input.link != null);
            try {
                const result = await request("preview", "POST", {
                    slots: JSON.stringify(rows),
                    scene: linked ? "" : String(sceneWidget()?.value ?? ""),
                });
                if (version !== previewVersion) return;
                preview.textContent = linked
                    ? (result.positive_prompt + "\n\n场景来自连线，运行后可查看最终文本。").trim()
                    : result.positive_prompt || "暂无正向提示词";
            } catch (error) {
                if (version === previewVersion) preview.textContent = error.message;
            }
        }, 120);
    };

    function changed() {
        node.graph?.setDirtyCanvas(true, true);
        updatePreview();
    }

    function editStack(change) {
        node.graph?.beforeChange?.(node);
        change();
        renderCards();
        changed();
        node.graph?.afterChange?.(node);
    }

    function checkbox(label, title) {
        const control = element("label");
        control.className = "personal-lora-toggle";
        const input = element("input", title);
        input.type = "checkbox";
        control.append(input, element("span", "", label));
        return { control, input };
    }

    function makeCard(row, index) {
        const number = index + 1;
        const card = element("article");
        card.className = "personal-lora-card";
        const label = element("span", "槽位 " + number, "槽位 " + number);
        label.className = "personal-lora-slot-label";
        const remove = element("button", "删除槽位 " + number, "×");
        remove.type = "button";
        remove.className = "personal-lora-remove";

        const select = element("select", "选择槽位 " + number + " 的 LoRA");
        if (row.name) select.title = row.name;
        select.append(new Option("选择 LoRA", ""));
        if (row.name && !catalog.loras.includes(row.name)) {
            select.append(new Option(row.name, row.name));
        }
        select.append(...catalog.loras.map((file) => new Option(file, file)));
        select.value = row.name;

        const enabled = checkbox("启用", "启用槽位 " + number);
        const inject = checkbox("带词", "为槽位 " + number + " 自动带词");
        const mode = element("select", "槽位 " + number + " 的触发词档位");
        mode.className = "personal-lora-mode";
        mode.append(new Option("简短", "short"), new Option("头部", "head"));
        const weight = element("input", "槽位 " + number + " 的权重");
        weight.type = "number";
        weight.min = -100;
        weight.max = 100;
        weight.step = "any";
        const weightControl = element("label");
        weightControl.className = "personal-lora-weight";
        weightControl.append(element("span", "", "权重"), weight);

        const edit = element("button", "编辑槽位 " + number + " 的触发词", "触发词");
        edit.type = "button";
        edit.className = "personal-lora-edit";
        const version = element("button", "检查槽位 " + number + " 的官网新版", "新版");
        version.type = "button";
        version.hidden = !catalog.website_updates ||
            !["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
        const actions = element("div");
        actions.className = "personal-lora-actions";
        actions.append(enabled.control, inject.control, mode, weightControl, edit, version);
        card.append(label, select, actions, remove);

        function updateCard() {
            enabled.input.checked = row.enabled;
            inject.input.checked = row.inject;
            mode.value = row.prompt_mode;
            mode.disabled = !row.name;
            weight.value = row.strength;
            edit.disabled = !row.name;
            version.disabled = !row.name;
            card.classList.toggle("is-enabled", Boolean(row.name && row.enabled && row.strength));
        }

        select.onchange = () => {
            row.name = select.value;
            select.title = row.name || "选择 LoRA";
            row.enabled = Boolean(row.name);
            if (row.enabled && row.strength === 0) row.strength = 0.9;
            updateCard();
            changed();
        };
        enabled.input.onchange = () => {
            row.enabled = enabled.input.checked;
            updateCard();
            changed();
        };
        inject.input.onchange = () => {
            row.inject = inject.input.checked;
            changed();
        };
        mode.onchange = () => {
            row.prompt_mode = mode.value;
            changed();
        };
        weight.oninput = () => {
            if (Number.isFinite(weight.valueAsNumber) && weight.validity.valid) {
                row.strength = weight.valueAsNumber;
                card.classList.toggle("is-enabled", Boolean(row.name && row.enabled && row.strength));
                changed();
            }
        };
        weight.onchange = () => { weight.value = row.strength; };
        edit.onclick = () => editTrigger(row, refreshCatalog, changed).catch((error) => {
            message.textContent = error.message;
        });
        version.onclick = () => {
            const original = row.name;
            openWebsiteImport((result, switchCurrent) => {
                if (disposed) return;
                if (switchCurrent && rows.includes(row) && row.name === original) {
                    editStack(() => switchSlotWeight(row, result.name, result.custom ?? {}));
                }
                reload();
            }, catalog.website_membership === true, { name: original });
        };
        remove.onclick = () => {
            editStack(() => rows.splice(index, 1));
            (cards.children[index]?.querySelector("select") ?? add).focus();
        };
        updateCard();
        return card;
    }

    function renderCards() {
        // Frontend files can update before the running backend is restarted.
        const reachedLegacyLimit = !catalog.dynamic_slots && rows.length >= 8;
        add.disabled = reachedLegacyLimit;
        const local = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
        website.hidden = reachedLegacyLimit || !catalog.website_import || !local;
        library.hidden = !catalog.website_library || !local;
        add.title = reachedLegacyLimit
            ? "主服务重启后可添加超过 8 个槽位"
            : "新增 LoRA 槽位";
        if (rows.length) {
            cards.replaceChildren(...rows.map(makeCard));
        } else {
            const empty = element("p", "", "没有 LoRA 槽位");
            empty.className = "personal-lora-empty";
            cards.replaceChildren(empty);
        }
    }

    async function refreshCatalog() {
        catalog = await request("catalog");
        if (!disposed) {
            renderCards();
            message.textContent = "";
            updatePreview();
        }
        return catalog;
    }

    library.onclick = () => openWebsiteLibrary(() => changes.dispatchEvent(new Event("catalog")));
    website.onclick = () => openWebsiteImport(({name: filename, prompt: text, head_prompt: head, prompt_mode: mode}) => {
        editStack(() => rows.push({
            ...blankRow(Boolean(text || head)), name: filename, enabled: true, prompt_mode: mode,
        }));
        reload();
    }, catalog.website_membership === true);
    add.onclick = () => {
        editStack(() => rows.push(blankRow(true)));
        cards.lastElementChild?.querySelector("select")?.focus();
    };
    const reload = () => refreshCatalog().catch((error) => { message.textContent = error.message; });
    details.ontoggle = updatePreview;

    const widget = node.addDOMWidget(name, "PERSONAL_LORA_SLOTS", panel, {
        hideOnZoom: false,
        getMinHeight: () => 215,
        getValue: () => JSON.stringify(rows),
        setValue: (value) => {
            try {
                rows = parseRows(value || initial);
                renderCards();
                updatePreview();
            } catch (error) {
                message.textContent = error.message;
            }
        },
    });
    widget.serialize = true;

    const scene = sceneWidget();
    scene?.inputEl?.classList.add("personal-lora-scene");
    scene?.inputEl?.addEventListener("input", updatePreview);
    if (scene) {
        const original = scene.callback;
        scene.callback = function (...args) {
            original?.apply(this, args);
            updatePreview();
        };
    }
    const executed = node.onExecuted;
    node.onExecuted = function (message) {
        executed?.call(this, message);
        if (message.positive_prompt?.length) {
            clearTimeout(timer);
            ++previewVersion;
            preview.textContent = message.positive_prompt[0] || "暂无正向提示词";
        }
    };
    const removed = node.onRemoved;
    node.onRemoved = function (...args) {
        disposed = true;
        changes.removeEventListener("catalog", reload);
        clearTimeout(timer);
        return removed?.apply(this, args);
    };
    renderCards();
    changes.addEventListener("catalog", reload);
    reload();
    return { widget };
}


app.registerExtension({
    name: "personal.lora.loader",
    getCustomWidgets: () => ({ PERSONAL_LORA_SLOTS: addSlots }),
});

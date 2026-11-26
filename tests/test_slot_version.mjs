import assert from "node:assert/strict";
import test from "node:test";
import { switchSlotWeight } from "../web/slot_version.js";

test("switching keeps slot settings and carries independent custom text", () => {
    const row = { name: "old.safetensors", enabled: false, inject: true, strength: -0.4,
        prompt_mode: "head", overrides: { "old.safetensors": "" }, head_overrides: {} };
    switchSlotWeight(row, "new.safetensors", { short: "library short", head: "library head" });
    assert.deepEqual(row, { name: "new.safetensors", enabled: false, inject: true, strength: -0.4,
        prompt_mode: "head", overrides: { "old.safetensors": "", "new.safetensors": "" },
        head_overrides: { "new.safetensors": "library head" } });
});

test("switching without custom text uses the new version presets", () => {
    const row = { name: "old.safetensors", overrides: {}, head_overrides: {} };
    switchSlotWeight(row, "new.safetensors", { short: null, head: null });
    assert.deepEqual(row, { name: "new.safetensors", overrides: {}, head_overrides: {} });
});

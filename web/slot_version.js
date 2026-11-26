export function switchSlotWeight(row, name, custom) {
    if (row.name === name) return;
    for (const [map, mode] of [[row.overrides, "short"], [row.head_overrides, "head"]]) {
        const text = Object.hasOwn(map, row.name) ? map[row.name] : custom[mode];
        if (typeof text === "string") map[name] = text;
    }
    row.name = name;
}

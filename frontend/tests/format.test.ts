import { describe, expect, it } from "vitest";

import { fileExtension, formatDuration, formatLimitMinutes, formatPercent, formatTimestamp } from "@/lib/format";

describe("时长格式化", () => {
  it("超过一小时用「小时/分/秒」，与后端说法一致", () => {
    expect(formatDuration(3771.1)).toBe("1小时02分51秒");
    expect(formatDuration(419.3)).toBe("6分59秒");
    expect(formatDuration(18.4)).toBe("0分18秒");
  });

  it("空值与负数不产生异常显示", () => {
    expect(formatDuration(null)).toBe("0分00秒");
    expect(formatDuration(undefined)).toBe("0分00秒");
    expect(formatDuration(-5)).toBe("0分00秒");
  });
});

describe("时间戳格式化", () => {
  it("逐字稿左侧时间戳固定为 时:分:秒", () => {
    expect(formatTimestamp(0)).toBe("00:00:00");
    expect(formatTimestamp(2538)).toBe("00:42:18");
    expect(formatTimestamp(3771.9)).toBe("01:02:51");
  });
});

describe("进度展示", () => {
  it("夹紧到 0–100，并保留一位小数", () => {
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(48.7)).toBe("48.7%");
    expect(formatPercent(100)).toBe("100%");
    expect(formatPercent(140)).toBe("100%");
    expect(formatPercent(null)).toBe("0%");
  });
});

describe("时长上限文案", () => {
  it("整小时说「小时」，否则说「分钟」（与后端一致）", () => {
    expect(formatLimitMinutes(120)).toBe("2 小时");
    expect(formatLimitMinutes(360)).toBe("6 小时");
    expect(formatLimitMinutes(180)).toBe("3 小时");
    expect(formatLimitMinutes(90)).toBe("90 分钟");
  });
});

describe("文件扩展名", () => {
  it("用于提交前的说明文案", () => {
    expect(fileExtension("播客.m4a")).toBe("M4A");
    expect(fileExtension("no-extension")).toBe("");
  });
});

/** 在发送动作前说明数据接收方、用途及本地保留边界。 */
import { useEffect, useState } from "react";
import { getJSON } from "../api/client";
import type { components } from "../api/generated";

export function DataNotice({ kind }: { kind: "decision" | "photo" | "planning" }): JSX.Element {
  const [receiver, setReceiver] = useState("你配置的模型 API（接收方地址尚未读取）");
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void getJSON<components["schemas"]["DataBoundaryInfo"]>("/api/data-boundaries", controller.signal)
      .then((value) => { if (active) setReceiver(value.llm_receiver); })
      .catch(() => { /* 地址不可读时保留明确的未知提示。 */ });
    return () => { active = false; controller.abort(); };
  }, []);
  return <aside className="page-sub data-notice" aria-label="发送与保留说明">
    {kind === "planning" ? <p>规划在本地计算天象；天气查询会把机位坐标发送给 Open-Meteo。赶场路线会把起终点发送给已配置的高德或百度地图服务；未配置时使用本地估算。</p>
      : <p>点击{kind === "photo" ? "选择照片即开始上传分析" : "发送或决策"}后，{kind === "photo"
        ? "最长边不超过 1024px 的 JPEG、选定 EXIF（含拍摄时间，不提取 GPS）、器材、补充说明与相关记忆/机位/计划文本（可能含坐标）"
        : "输入、会话上下文、相关档案与事件记忆、工具结果（可能含坐标）"}会发送至 {receiver}，用于{kind === "photo" ? "画面分析及拍摄建议" : "生成拍摄建议"}。{kind === "decision" ? "天气查询另将坐标发送给 Open-Meteo；路线查询可能发送起终点给配置的地图服务。" : "画面本身仍可能暴露位置或人物。"}</p>}
    <p>服务端保留会话、结果版本与 trace，快照可能含机位坐标；修改或删除收藏不会修改历史。暂无自动到期删除，浏览器也保留部分历史。照片临时文件在处理线程结束后删除，停止等待不会保证即时删除。清理本地数据不删除供应商副本。<a href="#/m1">查看本地数据与删除说明</a></p>
  </aside>;
}

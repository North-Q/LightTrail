/** M1 记忆页（E7-7 完善）：器材档案 GET/PUT /api/profile（真实读写）+ 偏好芯片（档案真实）+ 最近会话（真实）。 */

import { useEffect, useState } from "react";
import { getSession } from "../api/client";
import { useSources } from "../context/SourceContext";

interface Profile {
  camera_body: string;
  lenses: string[];
  preferences: string[];
  common_locations: string[];
  skill_level: string;
  favorite_spots: { name: string; latitude: number; longitude: number; subject?: string }[];
}

const EMPTY_PROFILE: Profile = {
  camera_body: "",
  lenses: [],
  preferences: [],
  common_locations: [],
  skill_level: "",
  favorite_spots: [],
};

function loadMetas(): { id: string; title: string; created: string }[] {
  try {
    return JSON.parse(localStorage.getItem("lt.sessions") ?? "[]") as { id: string; title: string; created: string }[];
  } catch {
    return [];
  }
}

export function M1Page() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [recent, setRecent] = useState<{ id: string; title: string; created: string; summary: string }[]>([]);
  const { addSource } = useSources();

  useEffect(() => {
    void (async () => {
      try {
        const resp = await fetch("/api/profile");
        if (resp.ok) {
          setProfile((await resp.json()) as Profile);
        } else {
          setProfile(EMPTY_PROFILE);
        }
      } catch {
        setProfile(EMPTY_PROFILE);
      }
      addSource({ name: "本地档案", note: "data/profile.json 档案读写" });
    })();

    // 最近会话（真实：本地记录 → 详情摘要）
    void (async () => {
      const metas = loadMetas().slice(0, 5);
      const rows: { id: string; title: string; created: string; summary: string }[] = [];
      for (const meta of metas) {
        let summary = meta.title;
        try {
          const body = await getSession(meta.id);
          const assistant = (body.session.history ?? []).filter((m) => m.role === "assistant").pop();
          if (assistant) {
            summary = assistant.content.slice(0, 60);
          }
        } catch {
          summary = `${meta.title}（详情暂不可读）`;
        }
        rows.push({ ...meta, summary });
      }
      setRecent(rows);
    })();
  }, [addSource]);

  async function save(): Promise<void> {
    if (!profile) {
      return;
    }
    setSaving(true);
    setMessage("");
    try {
      const resp = await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      });
      if (resp.ok) {
        setProfile((await resp.json()) as Profile);
        setMessage("已保存 ✓");
      } else {
        setMessage(`保存失败（${resp.status}）`);
      }
    } catch {
      setMessage("保存失败：请确认后端已启动");
    } finally {
      setSaving(false);
    }
  }

  if (!profile) {
    return <div className="container page-main"><p className="trace-empty">载入档案…</p></div>;
  }

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">M1 · 我的记忆</h1>
          <p className="page-sub">器材档案与偏好保存在本地档案；事件摘要来自最近会话。</p>
        </div>
      </header>

      <section className="grid-2">
        <div className="card">
          <span className="card-kicker">器材档案</span>
          <div className="gear-field">
            <label htmlFor="camera">机身</label>
            <input id="camera" value={profile.camera_body} onChange={(e) => setProfile({ ...profile, camera_body: e.target.value })} />
          </div>
          <div className="gear-field">
            <label htmlFor="lenses">镜头</label>
            <input id="lenses" value={profile.lenses.join("、")} onChange={(e) => setProfile({ ...profile, lenses: e.target.value.split(/[、,]/).map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="gear-field">
            <label htmlFor="level">水平</label>
            <input id="level" value={profile.skill_level} onChange={(e) => setProfile({ ...profile, skill_level: e.target.value })} />
          </div>
          <div className="gear-field">
            <label htmlFor="locs">常去机位</label>
            <input id="locs" value={profile.common_locations.join("、")} onChange={(e) => setProfile({ ...profile, common_locations: e.target.value.split(/[、,]/).map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="chip-row">
            <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存档案"}
            </button>
            {message ? <span className="page-sub" style={{ alignSelf: "center" }}>{message}</span> : null}
          </div>
        </div>

        <div className="card">
          <span className="card-kicker">偏好题材</span>
          {profile.preferences.length > 0 ? (
            <div className="pref-list">
              {profile.preferences.map((item) => (
                <span className="chip" key={item}>#{item}</span>
              ))}
            </div>
          ) : (
            <p className="trace-empty">暂无偏好——在下方「偏好」输入框（字段名 preferences）添加后保存。</p>
          )}
          <div className="gear-field" style={{ marginTop: 16 }}>
            <label htmlFor="prefs">偏好题材</label>
            <input id="prefs" value={profile.preferences.join("、")} onChange={(e) => setProfile({ ...profile, preferences: e.target.value.split(/[、,]/).map((s) => s.trim()).filter(Boolean) })} />
          </div>

          <span className="card-kicker" style={{ marginTop: 20, display: "block" }}>事件历史 · 最近会话（真实）</span>
          {recent.length === 0 ? (
            <p className="trace-empty">还没有会话——去「灵感」页发一条消息。</p>
          ) : (
            <ul className="event-list">
              {recent.map((item) => (
                <li key={item.id}>
                  <span className="src-time">{new Date(item.created).toLocaleString("zh-CN", { hour12: false })}</span>
                  <span>{item.title} — {item.summary}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="page-sub" style={{ marginTop: 12 }}>事件摘要来自最近会话；照片的 EXIF 复盘在「复盘」页进行。</p>
        </div>
      </section>
    </div>
  );
}

// Live AI Football Commentary — React chat UI (architecture §9).
// Commentary arrives as whole messages over SSE; the scoreboard is polled.

import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import htm from "htm";

const html = htm.bind(React.createElement);

const SCOREBOARD_POLL_MS = 10000;

function fixtureIdFromUrl() {
  const param = new URLSearchParams(window.location.search).get("fixture");
  return param === null ? null : Number(param);
}

function Scoreboard({ fixture }) {
  if (!fixture) return html`<header class="scoreboard">Waiting for fixture…</header>`;
  const clock =
    fixture.status === "FT" || fixture.status === "AET" || fixture.status === "PEN"
      ? "FULL TIME"
      : fixture.elapsed !== null
        ? `${fixture.elapsed}'`
        : fixture.status;
  return html`
    <header class="scoreboard">
      <div class="league">${fixture.league} · ${fixture.season}</div>
      <div class="score-row">
        <span class="team home">${fixture.home.name}</span>
        <span class="score">${fixture.home_goals ?? "–"} : ${fixture.away_goals ?? "–"}</span>
        <span class="team away">${fixture.away.name}</span>
      </div>
      <div class="clock">${clock}</div>
    </header>
  `;
}

function initials(name) {
  return name
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function Message({ message, commentator }) {
  const role = commentator?.role ?? "play_by_play";
  const name = commentator?.name ?? "Commentator";
  const side = role === "color" ? "right" : "left";
  return html`
    <div class=${`message ${side}`}>
      <div class=${`avatar ${role}`} title=${name}>${initials(name)}</div>
      <div class="bubble">
        <div class="speaker">${name}</div>
        <div class="text">${message.text}</div>
      </div>
    </div>
  `;
}

function App() {
  const [fixture, setFixture] = useState(null);
  const [commentators, setCommentators] = useState({});
  const [messages, setMessages] = useState([]);
  const [connected, setConnected] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    let eventSource = null;
    let pollTimer = null;
    let cancelled = false;

    async function boot() {
      const people = await (await fetch("/commentators")).json();
      if (cancelled) return;
      setCommentators(Object.fromEntries(people.map((p) => [p.id, p])));

      let id = fixtureIdFromUrl();
      if (id === null) {
        const fixtures = await (await fetch("/fixtures")).json();
        if (cancelled || fixtures.length === 0) return;
        id = fixtures[0].id;
      }

      async function refreshScoreboard() {
        const response = await fetch(`/fixtures/${id}`);
        if (response.ok && !cancelled) setFixture(await response.json());
      }
      await refreshScoreboard();
      pollTimer = setInterval(refreshScoreboard, SCOREBOARD_POLL_MS);

      eventSource = new EventSource(`/fixtures/${id}/commentary/stream`);
      eventSource.addEventListener("commentary", (event) => {
        const message = JSON.parse(event.data);
        setMessages((previous) =>
          previous.some((m) => m.id === message.id) ? previous : [...previous, message]
        );
      });
      eventSource.onopen = () => setConnected(true);
      eventSource.onerror = () => setConnected(false);
    }

    boot();
    return () => {
      cancelled = true;
      if (eventSource) eventSource.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return html`
    <div class="app">
      <${Scoreboard} fixture=${fixture} />
      <main class="chat">
        ${messages.length === 0 &&
        html`<div class="empty">The booth is warming up — commentary will appear here.</div>`}
        ${messages.map(
          (message) =>
            html`<${Message}
              key=${message.id}
              message=${message}
              commentator=${commentators[message.commentator_id]}
            />`
        )}
        <div ref=${endRef}></div>
      </main>
      <footer class="status">
        <span class=${connected ? "dot live" : "dot down"}></span>
        ${connected ? "live" : "reconnecting…"}
      </footer>
    </div>
  `;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);

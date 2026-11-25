import { useEffect, useRef, useState } from "react";
import { newSessionId, sendChat } from "./api.js";

const FAILURE_OPTIONS = [
  { value: "", label: "None (normal)" },
  { value: "key_1=429", label: "key_1 → 429" },
  { value: "key_1=500", label: "key_1 → 500" },
  { value: "key_1=429,key_2=500", label: "key_1 → 429, key_2 → 500" },
  { value: "*=429", label: "All keys → 429" },
];

function AssistantMessage({ content, metadata }) {
  const isCache = metadata.resolution_source === "LLM_CACHE";
  return (
    <div className="msg assistant" data-testid="assistant-message">
      <div className="bubble">{content}</div>
      <div className="meta">
        <span className={`badge ${isCache ? "cache" : "live"}`} data-testid="meta-source">
          {metadata.resolution_source}
        </span>
        <span className="meta-item" title="Model">
          <span data-testid="meta-model">{metadata.model}</span>
        </span>
        <span className="meta-item" title="Latency">
          <span data-testid="meta-latency">{metadata.latency_ms} ms</span>
        </span>
        <span className="meta-item" title="Input tokens for this request">
          in <span data-testid="meta-input-tokens">{metadata.input_tokens}</span>
        </span>
        <span className="meta-item" title="Output tokens for this reply">
          out <span data-testid="meta-output-tokens">{metadata.output_tokens}</span>
        </span>
        <span className="meta-item subtle" title="Key that answered">
          key: {metadata.key_id ?? "—"}
        </span>
        {metadata.fallback_attempts > 0 && (
          <span className="meta-item warn" title="Keys that failed before this one">
            fallbacks: {metadata.fallback_attempts}
          </span>
        )}
      </div>
    </div>
  );
}

export default function App() {
  // A fresh session on every page load.
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulateFailure, setSimulateFailure] = useState("");
  const [pending, setPending] = useState(false);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  async function handleSend(event) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || pending) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setPending(true);

    try {
      const data = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply, metadata: data.metadata },
      ]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        { role: "error", content: error.message, status: error.status },
      ]);
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  }

  function startNewChat() {
    setSessionId(newSessionId());
    setMessages([]);
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Lyftr AI</h1>
          <p className="session">
            Session <code>{sessionId}</code>
          </p>
        </div>
        <button type="button" className="ghost" onClick={startNewChat}>
          New chat
        </button>
      </header>

      <details className="settings">
        <summary>Settings</summary>
        <label>
          System prompt (optional, applies to this session)
          <textarea
            data-testid="system-prompt-input"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="e.g. You are a concise assistant that answers in one sentence."
            rows={2}
          />
        </label>
        <label>
          Simulate key failure (X-Simulate-Failure)
          <select value={simulateFailure} onChange={(e) => setSimulateFailure(e.target.value)}>
            {FAILURE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </details>

      <main className="chat">
        {messages.length === 0 && (
          <div className="empty">Ask anything to start the conversation.</div>
        )}
        {messages.map((m, i) =>
          m.role === "assistant" ? (
            <AssistantMessage key={i} content={m.content} metadata={m.metadata} />
          ) : m.role === "user" ? (
            <div key={i} className="msg user">
              <div className="bubble">{m.content}</div>
            </div>
          ) : (
            <div key={i} className="msg error" role="alert">
              <div className="bubble">
                <strong>{m.status ? `Error ${m.status}` : "Error"}:</strong> {m.content}
              </div>
            </div>
          )
        )}
        {pending && (
          <div className="msg assistant">
            <div className="bubble typing">Thinking…</div>
          </div>
        )}
        <div ref={endRef} />
      </main>

      <form className="composer" onSubmit={handleSend}>
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          rows={1}
          maxLength={4000}
          autoFocus
        />
        <button
          type="submit"
          data-testid="send-button"
          disabled={pending || !input.trim()}
        >
          Send
        </button>
      </form>
    </div>
  );
}

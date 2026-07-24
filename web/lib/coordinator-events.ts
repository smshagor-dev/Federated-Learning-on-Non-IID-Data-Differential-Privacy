import { API_BASE_URL } from "@/lib/api";
import type { CoordinatorEvent } from "@/types/api";

export type CoordinatorStreamStatus = "connecting" | "open" | "reconnecting" | "unavailable" | "closed";

// Server-Sent Events over fetch()+ReadableStream rather than the native
// EventSource API: EventSource cannot set an Authorization header, and
// GET /api/v1/coordinator/runs/{runId}/events requires a bearer token
// like every other authenticated route (see
// go/internal/transport/httpapi/server.go's withAuth wrapping). This is
// a real network parser, not a poll-in-disguise — it reads the same
// chunked text/event-stream body the Go handler writes.
export function subscribeToCoordinatorEvents(
  runId: string,
  token: string,
  handlers: {
    onEvent: (event: CoordinatorEvent) => void;
    onStatusChange?: (status: CoordinatorStreamStatus) => void;
  },
): () => void {
  const controller = new AbortController();
  let lastEventId = "";
  let stopped = false;

  async function run() {
    while (!stopped) {
      handlers.onStatusChange?.(lastEventId ? "reconnecting" : "connecting");
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/v1/coordinator/runs/${runId}/events${lastEventId ? `?after=${encodeURIComponent(lastEventId)}` : ""}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
          },
        );
        if (response.status === 503) {
          handlers.onStatusChange?.("unavailable");
          await sleep(3_000, controller.signal);
          continue;
        }
        if (!response.ok || !response.body) {
          await sleep(3_000, controller.signal);
          continue;
        }
        handlers.onStatusChange?.("open");
        await consumeStream(response.body, (event) => {
          lastEventId = event.event_id;
          handlers.onEvent(event);
        });
      } catch (error) {
        if (stopped || controller.signal.aborted) {
          return;
        }
        void error;
      }
      if (!stopped) {
        await sleep(1_500, controller.signal);
      }
    }
  }

  void run();

  return () => {
    stopped = true;
    controller.abort();
    handlers.onStatusChange?.("closed");
  };
}

async function consumeStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: CoordinatorEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseFrame(frame);
      if (parsed) {
        onEvent(parsed);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function parseFrame(frame: string): CoordinatorEvent | null {
  let eventType = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      data += line.slice("data:".length).trim();
    }
  }
  if (!data) {
    return null;
  }
  if (eventType === "coordinator-unavailable" || eventType === "coordinator-error") {
    return null;
  }
  try {
    return JSON.parse(data) as CoordinatorEvent;
  } catch {
    return null;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

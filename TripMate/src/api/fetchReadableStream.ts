import type { ConversationType, ServerDataType } from "@/types/index";

const baseUrl = "/api";

export type StreamHandlers = {
  onMessage?: (payload: ServerDataType) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
};

const parseSSEEvent = (eventText: string) => {
  const lines = eventText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const event = lines
    .filter((line) => line.startsWith("event:"))
    .map((line) => line.slice(6).trim())[0];

  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n");

  return { event, data };
};

export const sendMessageByFetchReadableStream = async (
  data: { chatMessages: ConversationType },
  handlers: StreamHandlers = {},
): Promise<void> => {
  const response = await fetch(`${baseUrl}/chatMessage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    throw new Error(`HTTP_${response.status}`);
  }

  if (!response.body) {
    throw new Error("ReadableStream is not available");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const eventText of events) {
        const { event, data: eventData } = parseSSEEvent(eventText);
        if (!eventData) continue;

        if (event === "done") {
          handlers.onDone?.();
          continue;
        }

        try {
          const payload = JSON.parse(eventData) as ServerDataType;
          handlers.onMessage?.(payload);
        } catch (error) {
          console.error("[fetch readable stream] parse error", error, eventData);
        }
      }
    }

    if (buffer.trim()) {
      const { event, data: eventData } = parseSSEEvent(buffer);
      if (event === "done") {
        handlers.onDone?.();
      } else if (eventData) {
        const payload = JSON.parse(eventData) as ServerDataType;
        handlers.onMessage?.(payload);
      }
    }
  } catch (error) {
    const streamError = error instanceof Error ? error : new Error("stream_error");
    handlers.onError?.(streamError);
    throw streamError;
  } finally {
    reader.releaseLock();
  }
};

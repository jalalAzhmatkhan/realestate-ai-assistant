import { useMutation } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'

/**
 * `POST /chat/messages` — the same entrypoint every chat-capable client calls
 * (`app/api/chat.py`, `app/schemas/chat.py`). Kept as one file rather than the
 * `types.ts`/`queries.ts`/`mutations.ts` split used by bookings/properties/users: there is
 * one endpoint, no filters, no pagination, and nothing here is ever listed or cached
 * across screens, so the split would only add files with one export each.
 */

/** Mirrors `MAX_MESSAGE_LENGTH` in `app/schemas/chat.py`. */
export const MAX_CHAT_MESSAGE_LENGTH = 4000

export interface ChatMessageRequest {
  /** `null` starts a new conversation; a real id continues one. */
  conversation_id: string | null
  message: string
}

/**
 * One tool the agent chose to call this turn, with what it passed and what it got back.
 * Rendering this is the entire point of the Chat Inspector screen: CLAUDE.md forbids any
 * hardcoded keyword/if-else routing to a tool, so the model's own tool-calling is the only
 * thing that decides `tool_calls` — and this is the one screen that makes that choice
 * visible instead of leaving it implicit in `reply` (`app/schemas/chat.py`, `ToolCallRecord`).
 */
export interface ChatToolCall {
  tool: string
  arguments: Record<string, unknown>
  result_summary: string
}

export interface ChatMessageResponse {
  conversation_id: string
  reply: string
  tool_calls: ChatToolCall[]
  created_at: string
}

/**
 * No cache to seed or invalidate on success: nothing else in the app lists or reads
 * conversations, so the only "cache" a turn belongs in is the transcript the Chat
 * Inspector screen keeps as its own component state, one entry per turn, appended to as
 * each `mutate` call settles.
 */
export function useSendChatMessage() {
  return useMutation<ChatMessageResponse, Error, ChatMessageRequest>({
    mutationFn: (payload) => apiClient.post<ChatMessageResponse>('/chat/messages', payload),
  })
}

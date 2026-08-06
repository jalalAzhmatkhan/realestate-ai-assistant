import { useRef, useState } from 'react'

import { useSendChatMessage, type ChatToolCall } from './chat'

export interface ChatTurn {
  id: string
  message: string
  status: 'pending' | 'done' | 'error'
  reply: string
  toolCalls: ChatToolCall[]
  error: unknown
}

function pendingTurn(id: string, message: string): ChatTurn {
  return { id, message, status: 'pending', reply: '', toolCalls: [], error: undefined }
}

/**
 * The turn/transcript state machine shared by the client-facing Chat page and the
 * admin-only Chat Inspector — both drive the same `POST /chat/messages` endpoint and the
 * same conversation-continuation contract, and differ only in what they render (the
 * inspector additionally shows `toolCalls`; the client-facing page never does).
 *
 * `conversation_id` resets together with the transcript on `startNewConversation`, never
 * separately: the backend keys history off `conversation_id` (`app/api/chat.py`,
 * `_resolve_conversation`), so clearing only the visible transcript would silently keep
 * feeding prior context to the agent on the next send.
 */
export function useChatTurns() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const send = useSendChatMessage()
  const nextTurnId = useRef(0)

  const pending = turns.some((turn) => turn.status === 'pending')

  function runTurn(turnId: string, message: string) {
    send.mutate(
      { conversation_id: conversationId, message },
      {
        onSuccess: (data) => {
          setConversationId(data.conversation_id)
          setTurns((prev) =>
            prev.map((turn) =>
              turn.id === turnId
                ? { ...turn, status: 'done', reply: data.reply, toolCalls: data.tool_calls }
                : turn,
            ),
          )
        },
        onError: (error) => {
          setTurns((prev) =>
            prev.map((turn) => (turn.id === turnId ? { ...turn, status: 'error', error } : turn)),
          )
        },
      },
    )
  }

  function sendMessage(rawMessage: string) {
    const message = rawMessage.trim()
    if (!message || pending) return

    nextTurnId.current += 1
    const turnId = `turn-${String(nextTurnId.current)}`
    setTurns((prev) => [...prev, pendingTurn(turnId, message)])
    runTurn(turnId, message)
  }

  function retryTurn(turn: ChatTurn) {
    setTurns((prev) => prev.map((t) => (t.id === turn.id ? { ...t, status: 'pending' } : t)))
    runTurn(turn.id, turn.message)
  }

  function startNewConversation() {
    setConversationId(null)
    setTurns([])
  }

  return { turns, pending, sendMessage, retryTurn, startNewConversation }
}

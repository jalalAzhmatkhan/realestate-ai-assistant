import { useState, type FormEvent, type KeyboardEvent } from 'react'

import Button from '@/components/ui/Button'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import { MAX_CHAT_MESSAGE_LENGTH } from '@/lib/chat/chat'
import { useChatTurns, type ChatTurn } from '@/lib/chat/useChatTurns'

/**
 * Admin-only internal debugging view: sends a message through the same
 * `POST /chat/messages` every chat-capable client calls, and always shows the
 * `tool_calls` trace the backend returns alongside the reply. This is explicitly not a
 * polished customer-facing chat UI — see `routes/ChatPage.tsx` for that — and staff
 * should never mistake this for it.
 *
 * The trace is never collapsed behind a toggle: it is the entire reason this screen
 * exists. CLAUDE.md forbids any hardcoded routing to a tool — the model decides via
 * native function calling — so this is the only place in the product where that decision
 * is directly observable rather than inferred from the reply text.
 *
 * Admin-only is a frontend-only restriction layered on top of an endpoint every role may
 * call (`app/api/chat.py` takes any authenticated `CurrentUser`). The router's
 * `<RoleGate>` keeps other roles off this path as UX, not as the security boundary.
 */
export default function ChatInspectorPage() {
  const { turns, pending, sendMessage, retryTurn, startNewConversation } = useChatTurns()
  const [draft, setDraft] = useState('')

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!draft.trim() || pending) return
    sendMessage(draft)
    setDraft('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter inserts a newline — a debugging tool is used all day, and
    // reaching for the mouse for every single test message would be the annoying part.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Chat Inspector</h1>
          <p className="text-sm text-slate-500">
            Internal — test the agent, not a customer view.
          </p>
        </div>
        <Button
          variant="secondary"
          disabled={pending}
          onClick={() => {
            startNewConversation()
            setDraft('')
          }}
        >
          New conversation
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <label htmlFor="chat-inspector-message" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-inspector-message"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value)
          }}
          onKeyDown={handleKeyDown}
          maxLength={MAX_CHAT_MESSAGE_LENGTH}
          disabled={pending}
          rows={2}
          placeholder="Ask the agent something a client might ask…"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:bg-slate-50"
        />
        <Button type="submit" disabled={pending || draft.trim().length === 0}>
          {pending ? 'Sending…' : 'Send'}
        </Button>
      </form>

      {turns.length === 0 ? (
        <EmptyState
          title="No messages yet"
          description="Send a message to see how the agent replies and which tools it chooses to call."
        />
      ) : (
        <div
          role="log"
          aria-live="polite"
          aria-label="Conversation transcript"
          className="max-h-[65vh] space-y-4 overflow-y-auto"
        >
          {turns.map((turn) => (
            <TurnView
              key={turn.id}
              turn={turn}
              onRetry={() => {
                retryTurn(turn)
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function TurnView({ turn, onRetry }: { turn: ChatTurn; onRetry: () => void }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <p className="text-sm text-slate-900">
        <span className="font-semibold">You:</span> {turn.message}
      </p>

      {turn.toolCalls.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {turn.toolCalls.map((call, index) => (
            <li
              key={index}
              className="whitespace-pre-wrap break-all rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700"
            >
              {`Agent (tool call: ${call.tool}, args: ${JSON.stringify(call.arguments)}) → ${call.result_summary}`}
            </li>
          ))}
        </ul>
      ) : null}

      {turn.status === 'pending' ? (
        <div className="mt-2">
          <LoadingState variant="spinner" label="Waiting for the agent…" />
        </div>
      ) : turn.status === 'error' ? (
        <div className="mt-2">
          <ErrorState title="The agent could not reply" error={turn.error} onRetry={onRetry} />
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-900">
          <span className="font-semibold">Agent:</span> {turn.reply}
        </p>
      )}
    </div>
  )
}

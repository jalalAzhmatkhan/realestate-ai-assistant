import { useState, type FormEvent, type KeyboardEvent } from 'react'

import Button from '@/components/ui/Button'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import { MAX_CHAT_MESSAGE_LENGTH } from '@/lib/chat/chat'
import { useChatTurns, type ChatTurn } from '@/lib/chat/useChatTurns'

/**
 * The actual product: a client talks to the agent about FAQs, listings, and viewings.
 * Every role can reach this screen — `app/api/chat.py` takes any authenticated
 * `CurrentUser`, no role restriction, and the agent's own tools re-scope themselves per
 * caller (e.g. `SearchProperty`/`BookViewing` already narrow to what an `agent` or
 * `client` may see/do). This is deliberately not the Chat Inspector: no `tool_calls`
 * trace here — that is an internal debugging affordance, not something an end user
 * should see or need to understand.
 */
export default function ChatPage() {
  const { turns, pending, sendMessage, retryTurn, startNewConversation } = useChatTurns()
  const [draft, setDraft] = useState('')

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!draft.trim() || pending) return
    sendMessage(draft)
    setDraft('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Chat</h1>
          <p className="text-sm text-slate-500">
            Ask about listings, policies, or your viewings — or book one right here.
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

      {turns.length === 0 ? (
        <EmptyState
          title="Start the conversation"
          description="Try “what documents do I need to rent a property?” or “book a viewing for this Saturday”."
        />
      ) : (
        <div
          role="log"
          aria-live="polite"
          aria-label="Conversation transcript"
          className="max-h-[60vh] flex-1 space-y-3 overflow-y-auto"
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

      <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <label htmlFor="chat-message" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-message"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value)
          }}
          onKeyDown={handleKeyDown}
          maxLength={MAX_CHAT_MESSAGE_LENGTH}
          disabled={pending}
          rows={2}
          placeholder="Type your message…"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:bg-slate-50"
        />
        <Button type="submit" disabled={pending || draft.trim().length === 0}>
          {pending ? 'Sending…' : 'Send'}
        </Button>
      </form>
    </div>
  )
}

function TurnView({ turn, onRetry }: { turn: ChatTurn; onRetry: () => void }) {
  return (
    <div className="space-y-2">
      <div className="ml-auto max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
        {turn.message}
      </div>

      {turn.status === 'pending' ? (
        <div className="max-w-[80%]">
          <LoadingState variant="spinner" label="Thinking…" />
        </div>
      ) : turn.status === 'error' ? (
        <div className="max-w-[80%]">
          <ErrorState title="Couldn't get a reply" error={turn.error} onRetry={onRetry} />
        </div>
      ) : (
        <div className="mr-auto max-w-[80%] rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900">
          {turn.reply}
        </div>
      )}
    </div>
  )
}

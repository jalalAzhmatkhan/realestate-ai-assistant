import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { json, mockApi, requestBody, requestedUrls } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'

const SEND = 'POST /api/v1/chat/messages'

function renderChat(role: 'admin' | 'agent' | 'client' = 'client') {
  return renderApp({ user: buildUser(role), initialEntries: ['/chat'] })
}

async function sendMessage(text: string) {
  await userEvent.type(screen.getByLabelText('Message'), text)
  await userEvent.click(screen.getByRole('button', { name: 'Send' }))
}

describe('ChatPage', () => {
  it('is reachable by every role, not just staff', async () => {
    renderChat('client')

    await screen.findByRole('heading', { name: 'Chat' })
    expect(screen.getByTestId('empty-state')).toBeVisible()
  })

  it('shows an empty state before any message is sent', () => {
    renderChat()

    expect(screen.getByTestId('empty-state')).toBeVisible()
  })

  it('sends a message with a null conversation_id on the first turn', async () => {
    const fetchMock = mockApi({
      [SEND]: () =>
        json(200, {
          conversation_id: 'conv-1',
          reply: 'Sure, here are three listings.',
          tool_calls: [],
          created_at: '2026-08-06T03:00:00Z',
        }),
    })
    renderChat()

    await sendMessage('What documents do I need to rent a property?')

    await waitFor(() => {
      expect(requestBody(fetchMock)).toEqual({
        conversation_id: null,
        message: 'What documents do I need to rent a property?',
      })
    })
  })

  it('shows the user message, a pending state, then the reply once it resolves', async () => {
    let resolve: (response: Response) => void = () => undefined
    const pending = new Promise<Response>((r) => {
      resolve = r
    })
    vi.stubGlobal('fetch', vi.fn(() => pending))
    renderChat()

    await sendMessage('Can I bring my cat?')

    expect(await screen.findByText('Can I bring my cat?')).toBeVisible()
    expect(screen.getByTestId('loading-state')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Sending…' })).toBeDisabled()

    resolve(
      json(200, {
        conversation_id: 'conv-2',
        reply: 'Pet policy varies by listing — small to medium pets are generally allowed with a deposit.',
        tool_calls: [
          { tool: 'search_faq', arguments: { query: 'pet rules' }, result_summary: '1 result(s)' },
        ],
        created_at: '2026-08-06T03:00:00Z',
      }),
    )

    expect(
      await screen.findByText(
        'Pet policy varies by listing — small to medium pets are generally allowed with a deposit.',
      ),
    ).toBeVisible()
    expect(screen.queryByTestId('loading-state')).not.toBeInTheDocument()
  })

  it('never shows the tool_calls trace — this is the client-facing screen, not the inspector', async () => {
    mockApi({
      [SEND]: () =>
        json(200, {
          conversation_id: 'conv-3',
          reply: 'Booked for Saturday at 10:00.',
          tool_calls: [
            {
              tool: 'schedule_viewing',
              arguments: { property_id: 'prop-1', slot_time: '2026-08-08T03:00:00Z' },
              result_summary: 'confirmed',
            },
          ],
          created_at: '2026-08-06T03:00:00Z',
        }),
    })
    renderChat()

    await sendMessage('Book a viewing for prop-1 on Saturday morning')

    await screen.findByText('Booked for Saturday at 10:00.')
    expect(screen.queryByText(/tool call:/)).not.toBeInTheDocument()
    expect(screen.queryByText('schedule_viewing')).not.toBeInTheDocument()
  })

  it('continues the same conversation_id across turns', async () => {
    let call = 0
    const fetchMock = mockApi({
      [SEND]: () => {
        call += 1
        return json(200, {
          conversation_id: 'conv-5',
          reply: call === 1 ? 'first reply' : 'second reply',
          tool_calls: [],
          created_at: '2026-08-06T03:00:00Z',
        })
      },
    })
    renderChat()

    await sendMessage('first message')
    await screen.findByText('first reply')

    await sendMessage('second message')
    await waitFor(() => {
      expect(requestBody(fetchMock, 1)).toEqual({
        conversation_id: 'conv-5',
        message: 'second message',
      })
    })
  })

  it('shows an error state on a failed send and retries the same message', async () => {
    let attempt = 0
    const fetchMock = mockApi({
      [SEND]: () => {
        attempt += 1
        return attempt === 1
          ? json(503, {
              detail: {
                code: 'agent_unavailable',
                message: 'The assistant is temporarily unavailable. Please try again in a moment.',
              },
            })
          : json(200, {
              conversation_id: 'conv-6',
              reply: 'Recovered.',
              tool_calls: [],
              created_at: '2026-08-06T03:00:00Z',
            })
      },
    })
    renderChat()

    await sendMessage('a question')

    expect(await screen.findByTestId('error-state')).toBeVisible()
    expect(
      screen.getByText('The assistant is temporarily unavailable. Please try again in a moment.'),
    ).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Recovered.')).toBeVisible()
    expect(fetchMock.mock.calls.length).toBe(2)
    expect(requestBody(fetchMock, 1)).toEqual({ conversation_id: null, message: 'a question' })
  })

  it('resets both the conversation id and the visible transcript on New conversation', async () => {
    const fetchMock = mockApi({
      [SEND]: () =>
        json(200, {
          conversation_id: 'conv-7',
          reply: 'ok',
          tool_calls: [],
          created_at: '2026-08-06T03:00:00Z',
        }),
    })
    renderChat()

    await sendMessage('first message')
    await screen.findByText('ok')

    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))

    expect(screen.queryByText('first message')).not.toBeInTheDocument()
    expect(screen.getByTestId('empty-state')).toBeVisible()

    await sendMessage('after reset')
    await waitFor(() => {
      expect(requestBody(fetchMock, 1)).toEqual({ conversation_id: null, message: 'after reset' })
    })
    expect(requestedUrls(fetchMock).filter((url) => url.pathname === '/api/v1/chat/messages')).toHaveLength(2)
  })

  it('never sends a blank or whitespace-only message', async () => {
    mockApi({ [SEND]: () => json(200, { conversation_id: 'x', reply: 'ok', tool_calls: [], created_at: '2026-08-06T03:00:00Z' }) })
    renderChat()

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    await userEvent.type(screen.getByLabelText('Message'), '   ')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })
})

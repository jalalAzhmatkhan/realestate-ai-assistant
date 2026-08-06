import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { json, mockApi, requestBody, requestedUrls } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'

const SEND = 'POST /api/v1/chat/messages'

function renderInspector() {
  return renderApp({ user: buildUser('admin'), initialEntries: ['/chat-inspector'] })
}

async function sendMessage(text: string) {
  await userEvent.type(screen.getByLabelText('Message'), text)
  await userEvent.click(screen.getByRole('button', { name: 'Send' }))
}

describe('ChatInspectorPage', () => {
  it('shows an empty state before any message is sent', () => {
    renderInspector()

    expect(screen.getByTestId('empty-state')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Chat Inspector' })).toBeVisible()
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
    renderInspector()

    await sendMessage('Show me 2-bedroom apartments in Jakarta')

    await waitFor(() => {
      expect(requestBody(fetchMock)).toEqual({
        conversation_id: null,
        message: 'Show me 2-bedroom apartments in Jakarta',
      })
    })
  })

  it('shows the user turn, a pending state, then the reply once it resolves', async () => {
    let resolve: (response: Response) => void = () => undefined
    const pending = new Promise<Response>((r) => {
      resolve = r
    })
    vi.stubGlobal('fetch', vi.fn(() => pending))
    renderInspector()

    await sendMessage('What is your cancellation policy?')

    expect(await screen.findByText(/You:/)).toBeVisible()
    expect(screen.getByText('What is your cancellation policy?')).toBeVisible()
    expect(screen.getByTestId('loading-state')).toBeVisible()
    // Cannot fire a second message while one is in flight.
    expect(screen.getByRole('button', { name: 'Sending…' })).toBeDisabled()

    resolve(
      json(200, {
        conversation_id: 'conv-2',
        reply: 'You can cancel any viewing up to 24 hours in advance.',
        tool_calls: [],
        created_at: '2026-08-06T03:00:00Z',
      }),
    )

    expect(
      await screen.findByText('You can cancel any viewing up to 24 hours in advance.'),
    ).toBeVisible()
    expect(screen.queryByTestId('loading-state')).not.toBeInTheDocument()
  })

  it('renders the tool-call trace in the documented format, always visible', async () => {
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
    renderInspector()

    await sendMessage('Book a viewing for prop-1 on Saturday morning')

    expect(
      await screen.findByText(
        'Agent (tool call: schedule_viewing, args: {"property_id":"prop-1","slot_time":"2026-08-08T03:00:00Z"}) → confirmed',
      ),
    ).toBeVisible()
    // Not hidden behind any toggle or expand control.
    expect(screen.queryByRole('button', { name: /expand|show trace|details/i })).not.toBeInTheDocument()
  })

  it('renders no tool-call lines when the agent answered without calling a tool', async () => {
    mockApi({
      [SEND]: () =>
        json(200, {
          conversation_id: 'conv-4',
          reply: 'Hello! How can I help you today?',
          tool_calls: [],
          created_at: '2026-08-06T03:00:00Z',
        }),
    })
    renderInspector()

    await sendMessage('hi')

    await screen.findByText('Hello! How can I help you today?')
    expect(screen.queryByText(/tool call:/)).not.toBeInTheDocument()
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
    renderInspector()

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
          ? json(503, { detail: { code: 'agent_unavailable', message: 'The assistant is temporarily unavailable. Please try again in a moment.' } })
          : json(200, {
              conversation_id: 'conv-6',
              reply: 'Recovered.',
              tool_calls: [],
              created_at: '2026-08-06T03:00:00Z',
            })
      },
    })
    renderInspector()

    await sendMessage('a question')

    expect(await screen.findByTestId('error-state')).toBeVisible()
    expect(
      screen.getByText('The assistant is temporarily unavailable. Please try again in a moment.'),
    ).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Recovered.')).toBeVisible()
    expect(fetchMock.mock.calls.length).toBe(2)
    // The retry resent the exact same message the first attempt failed on.
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
    renderInspector()

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
    renderInspector()

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    await userEvent.type(screen.getByLabelText('Message'), '   ')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })
})

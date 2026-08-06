import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { json, mockApi, requestBody } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'

/**
 * Adversarial companion to `ChatInspectorPage.test.tsx`, which covers the happy path.
 * Kept as a separate file for the same reason `UsersPage.contract.test.tsx` is: these
 * assert against the *contract* — who may reach the screen, what goes out on the wire —
 * rather than against what the component currently renders.
 *
 * Two cases here are the reason the file exists. The reset/in-flight race is checked by
 * bypassing userEvent's pointer-events guard with `fireEvent`, because a control that is
 * merely styled as disabled would pass a userEvent-only test. And every role case
 * asserts `fetch` was never called, because an empty screen proves nothing about whether
 * the request went out.
 */

const SEND = 'POST /api/v1/chat/messages'
const PATH = '/chat-inspector'

function renderAs(role: 'admin' | 'agent' | 'client') {
  return renderApp({ user: buildUser(role), initialEntries: [PATH] })
}

async function sendMessage(text: string) {
  await userEvent.type(screen.getByLabelText('Message'), text)
  await userEvent.click(screen.getByRole('button', { name: 'Send' }))
}

function deferred() {
  let resolve: (r: Response) => void = () => undefined
  const promise = new Promise<Response>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

function ok(overrides: Record<string, unknown> = {}) {
  return json(200, {
    conversation_id: 'conv-A',
    reply: 'ok',
    tool_calls: [],
    created_at: '2026-08-06T03:00:00Z',
    ...overrides,
  })
}

describe('ChatInspectorPage RBAC', () => {
  it('a client never reaches the inspector and fires no chat request', async () => {
    // `client` reaches the shell now (they have their own real Chat screen), but the
    // inspector's own <RoleGate allow={['admin']}> still bounces them, same as an agent.
    const fetchMock = mockApi({ [SEND]: () => ok() })
    renderAs('client')

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Chat Inspector' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Message')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('an agent is bounced and fires no chat request', async () => {
    const fetchMock = mockApi({ [SEND]: () => ok() })
    renderAs('agent')

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.queryByLabelText('Message')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('hides the nav link from an agent but shows it to an admin', async () => {
    mockApi({ [SEND]: () => ok() })
    const { unmount } = renderApp({ user: buildUser('agent'), initialEntries: ['/properties'] })
    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.queryByRole('link', { name: /Chat Inspector/ })).not.toBeInTheDocument()
    unmount()

    renderAs('admin')
    expect(await screen.findByRole('link', { name: /Chat Inspector/ })).toBeVisible()
  })

  it('an unknown role cannot reach the inspector', async () => {
    mockApi({ [SEND]: () => ok() })
    renderApp({
      user: buildUser('admin', { role: 'superadmin' as never }),
      initialEntries: [PATH],
    })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
  })
})

describe('ChatInspectorPage New conversation race', () => {
  it('cannot be clicked while a send is in flight, even bypassing pointer-events', async () => {
    const d = deferred()
    vi.stubGlobal('fetch', vi.fn(() => d.promise))
    renderAs('admin')

    await sendMessage('first message')
    await screen.findByTestId('loading-state')

    const newConv = screen.getByRole('button', { name: 'New conversation' })
    expect(newConv).toBeDisabled()

    // fireEvent bypasses userEvent's pointer-events guard; a disabled button must still
    // not run its handler. If it did, the in-flight turn would be orphaned.
    fireEvent.click(newConv)

    expect(screen.getByText('first message')).toBeVisible()
    expect(screen.queryByTestId('empty-state')).not.toBeInTheDocument()

    d.resolve(ok({ conversation_id: 'conv-race' }))
    expect(await screen.findByText('ok')).toBeVisible()
  })

  it('an in-flight response can never land on an already-reset transcript', async () => {
    const d = deferred()
    vi.stubGlobal('fetch', vi.fn(() => d.promise))
    renderAs('admin')

    await sendMessage('in flight')
    await screen.findByTestId('loading-state')

    // Invariant under test: a request in flight always implies a pending turn, which
    // always disables the reset. Both controls must be locked.
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeDisabled()
    expect(screen.getByLabelText('Message')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Sending…' })).toBeDisabled()

    d.resolve(ok({ conversation_id: 'conv-late', reply: 'late reply' }))
    expect(await screen.findByText('late reply')).toBeVisible()
    // The reply belongs to the turn that asked for it, not to a fresh transcript.
    expect(screen.getByText('in flight')).toBeVisible()
  })

  it('reset unlocks only after a failure, and the failed turn goes with it', async () => {
    mockApi({ [SEND]: () => json(503, { detail: { code: 'agent_unavailable', message: 'down' } }) })
    renderAs('admin')

    await sendMessage('doomed')
    await screen.findByTestId('error-state')

    const newConv = screen.getByRole('button', { name: 'New conversation' })
    expect(newConv).toBeEnabled()
    await userEvent.click(newConv)

    expect(screen.queryByText('doomed')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByTestId('empty-state')).toBeVisible()
  })

  it('sends conversation_id null on the wire after a reset mid-conversation', async () => {
    const fetchMock = mockApi({ [SEND]: () => ok({ conversation_id: 'conv-live' }) })
    renderAs('admin')

    await sendMessage('turn one')
    await screen.findByText('ok')
    await sendMessage('turn two')
    await waitFor(() => {
      expect(requestBody(fetchMock, 1)).toEqual({
        conversation_id: 'conv-live',
        message: 'turn two',
      })
    })

    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    await sendMessage('after reset')

    await waitFor(() => {
      expect(requestBody(fetchMock, 2)).toEqual({ conversation_id: null, message: 'after reset' })
    })
    expect(fetchMock.mock.calls.length).toBe(3)
  })

  it('clears a half-typed draft on reset so it cannot leak into the new conversation', async () => {
    mockApi({ [SEND]: () => ok() })
    renderAs('admin')

    await userEvent.type(screen.getByLabelText('Message'), 'half typed')
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))

    expect(screen.getByLabelText('Message')).toHaveValue('')
  })
})

describe('ChatInspectorPage retry fidelity', () => {
  it('retries with the same message and does not disturb other turns', async () => {
    let attempt = 0
    const fetchMock = mockApi({
      [SEND]: () => {
        attempt += 1
        // First turn succeeds; second turn fails once then succeeds on retry.
        if (attempt === 1) return ok({ conversation_id: 'conv-R', reply: 'first reply' })
        if (attempt === 2) return json(503, { detail: { code: 'agent_unavailable', message: 'down' } })
        return ok({ conversation_id: 'conv-R', reply: 'second reply' })
      },
    })
    renderAs('admin')

    await sendMessage('message one')
    await screen.findByText('first reply')
    await sendMessage('message two')
    await screen.findByTestId('error-state')

    // The healthy turn is untouched while its neighbour is in an error state.
    expect(screen.getByText('first reply')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('second reply')).toBeVisible()
    expect(screen.getByText('first reply')).toBeVisible()
    expect(screen.queryByTestId('error-state')).not.toBeInTheDocument()
    expect(requestBody(fetchMock, 2)).toEqual({
      conversation_id: 'conv-R',
      message: 'message two',
    })
  })

  it('locks the composer while a retry is in flight', async () => {
    let attempt = 0
    const d = deferred()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        attempt += 1
        return attempt === 1
          ? Promise.resolve(json(503, { detail: { code: 'agent_unavailable', message: 'down' } }))
          : d.promise
      }),
    )
    renderAs('admin')

    await sendMessage('will fail')
    await screen.findByTestId('error-state')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'New conversation' })).toBeDisabled()
    })
    expect(screen.getByLabelText('Message')).toBeDisabled()

    d.resolve(ok({ reply: 'recovered' }))
    expect(await screen.findByText('recovered')).toBeVisible()
  })

  it('retries a stale error turn into the live conversation, not the id it first failed with', async () => {
    let attempt = 0
    const fetchMock = mockApi({
      [SEND]: () => {
        attempt += 1
        if (attempt === 1) return json(503, { detail: { code: 'agent_unavailable', message: 'down' } })
        return ok({ conversation_id: 'conv-NEW', reply: `reply ${String(attempt)}` })
      },
    })
    renderAs('admin')

    // Turn 1 fails before any conversation id exists.
    await sendMessage('failed first')
    await screen.findByTestId('error-state')
    // Turn 2 succeeds and establishes conv-NEW.
    await sendMessage('succeeded second')
    await screen.findByText('reply 2')
    // Now retry the stale turn 1.
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByText('reply 3')

    // Deliberate: `runTurn` reads the *current* conversation id, so the retry joins the
    // live conversation rather than re-sending the `null` it originally failed with —
    // which would silently fork a second conversation the tester never asked for. The
    // cost is that this turn renders above a turn it was actually delivered after.
    expect(requestBody(fetchMock, 2)).toEqual({
      conversation_id: 'conv-NEW',
      message: 'failed first',
    })
  })
})

describe('ChatInspectorPage tool-call trace fidelity', () => {
  it('renders every tool call in the order the backend returned them', async () => {
    mockApi({
      [SEND]: () =>
        ok({
          reply: 'Booked.',
          tool_calls: [
            { tool: 'search_property', arguments: { city: 'Jakarta' }, result_summary: '3 result(s)' },
            { tool: 'search_faq', arguments: { query: 'deposit' }, result_summary: '1 result(s)' },
            {
              tool: 'schedule_viewing',
              arguments: { property_id: 'p-1' },
              result_summary: 'confirmed',
            },
          ],
        }),
    })
    renderAs('admin')

    await sendMessage('find me a place then book it')
    await screen.findByText('Booked.')

    const lines = screen.getAllByText(/tool call:/).map((el) => el.textContent ?? '')
    expect(lines).toHaveLength(3)
    expect(lines[0]).toContain('search_property')
    expect(lines[1]).toContain('search_faq')
    expect(lines[2]).toContain('schedule_viewing')
  })

  it('renders no placeholder, bracket, or empty list for a zero-tool turn', async () => {
    renderAs('admin')
    mockApi({ [SEND]: () => ok({ reply: 'Hello! How can I help?', tool_calls: [] }) })

    await sendMessage('hi there')
    await screen.findByText('Hello! How can I help?')

    const transcript = screen.getByRole('log', { name: 'Conversation transcript' })
    expect(screen.queryByText(/tool call/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no tools/i)).not.toBeInTheDocument()
    // No empty <ul> shell left behind where the trace would have been.
    expect(transcript.querySelector('ul')).toBeNull()
    expect(transcript.textContent).not.toMatch(/\[\s*\]/)
    // Exactly the two lines a zero-tool turn should have: the message and the reply.
    expect(transcript.textContent).toBe('You: hi thereAgent: Hello! How can I help?')
  })

  it('renders a tool call with empty arguments without crashing', async () => {
    mockApi({
      [SEND]: () =>
        ok({ reply: 'Handed off.', tool_calls: [{ tool: 'escalate_to_human', arguments: {}, result_summary: 'escalated' }] }),
    })
    renderAs('admin')

    await sendMessage('I want a human')
    expect(
      await screen.findByText('Agent (tool call: escalate_to_human, args: {}) → escalated'),
    ).toBeVisible()
  })

  it('does not execute HTML/script-shaped content in a tool result', async () => {
    mockApi({
      [SEND]: () =>
        ok({
          reply: '<img src=x onerror="alert(1)">',
          tool_calls: [
            {
              tool: 'search_faq',
              arguments: { query: '<script>alert(1)</script>' },
              result_summary: '<b>1</b> result(s)',
            },
          ],
        }),
    })
    const { container } = renderAs('admin')

    await sendMessage('xss probe')
    await screen.findByText(/onerror/)

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
  })
})

describe('ChatInspectorPage message length boundary', () => {
  it('caps the textarea at the backend limit and sends the full 4000 characters', async () => {
    const fetchMock = mockApi({ [SEND]: () => ok() })
    renderAs('admin')

    const textarea = screen.getByLabelText('Message')
    expect(textarea).toHaveAttribute('maxLength', '4000')

    // Paste rather than type: 4001 keystrokes is far too slow, and paste is also the
    // realistic way a tester would put a long transcript into this box.
    await userEvent.click(textarea)
    await userEvent.paste('x'.repeat(4001))

    expect((textarea as HTMLTextAreaElement).value).toHaveLength(4000)

    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => {
      const body = requestBody(fetchMock) as { message: string; conversation_id: null }
      expect(body.message).toHaveLength(4000)
      expect(body.conversation_id).toBeNull()
    })
  })

  it('surfaces a backend 422 for an over-length message rather than swallowing it', async () => {
    mockApi({
      [SEND]: () =>
        json(422, {
          detail: { code: 'validation_error', message: 'message is too long.' },
        }),
    })
    renderAs('admin')

    await sendMessage('anything')

    expect(await screen.findByTestId('error-state')).toBeVisible()
    expect(screen.getByText('message is too long.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  })
})

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { json, mockApi, requestedUrls } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'
import {
  buildEvalRunDetail,
  buildEvalRunPage,
  buildEvalRunSummary,
  buildFailedEvalRunSummary,
  buildRetrievalLog,
  buildRetrievalLogPage,
} from '@/test/observabilityFixtures'

const LOG_PATH = '/api/v1/observability/retrieval-logs'
const RUNS_PATH = '/api/v1/observability/retrieval/eval-runs'
const LOG_LIST = `GET ${LOG_PATH}`
const RUNS_LIST = `GET ${RUNS_PATH}`
const RUN_TRIGGER = `POST ${RUNS_PATH}`

function renderObservability(entry = '/observability') {
  return renderApp({ user: buildUser('admin'), initialEntries: [entry] })
}

function logsOf(...logs: ReturnType<typeof buildRetrievalLog>[]) {
  return () => json(200, buildRetrievalLogPage(logs))
}

function runsOf(...runs: ReturnType<typeof buildEvalRunSummary>[]) {
  return () => json(200, buildEvalRunPage(runs))
}

function lastQuery(fetchMock: ReturnType<typeof mockApi>, pathname: string): URLSearchParams {
  const calls = requestedUrls(fetchMock).filter((url) => url.pathname === pathname)
  return calls[calls.length - 1]?.searchParams ?? new URLSearchParams()
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string, pathname: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => new URL(input).pathname === pathname && (init?.method ?? 'GET') === method,
  )
  const raw = call?.[1]?.body
  return typeof raw === 'string' ? (JSON.parse(raw) as unknown) : undefined
}

describe('ObservabilityPage RBAC', () => {
  it('a client never reaches the page and fires no observability request', async () => {
    // `client` reaches the shell now, but this page's own <RoleGate allow={['admin']}>
    // still bounces them, same as an agent.
    const fetchMock = mockApi({ [LOG_LIST]: logsOf(), [RUNS_LIST]: runsOf() })
    renderApp({ user: buildUser('client'), initialEntries: ['/observability'] })

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'RAG Observability' })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('an agent is bounced to the dashboard and fires no observability request', async () => {
    const fetchMock = mockApi({ [LOG_LIST]: logsOf(), [RUNS_LIST]: runsOf() })
    renderApp({ user: buildUser('agent'), initialEntries: ['/observability'] })

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.getByText('You do not have access to /observability.')).toBeVisible()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('hides the nav link from an agent but shows it to an admin', async () => {
    mockApi({ [LOG_LIST]: logsOf(), [RUNS_LIST]: runsOf() })
    const { unmount } = renderApp({ user: buildUser('agent'), initialEntries: ['/properties'] })
    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.queryByRole('link', { name: /Observability/ })).not.toBeInTheDocument()
    unmount()

    renderObservability()
    expect(await screen.findByRole('link', { name: /Observability/ })).toBeVisible()
  })

  it('an unknown role cannot reach the page', async () => {
    mockApi({ [LOG_LIST]: logsOf(), [RUNS_LIST]: runsOf() })
    renderApp({
      user: buildUser('admin', { role: 'superadmin' as never }),
      initialEntries: ['/observability'],
    })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
  })
})

describe('ObservabilityPage retrieval log tab', () => {
  it('is the default tab and renders the logs the backend returned', async () => {
    mockApi({
      [LOG_LIST]: logsOf(
        buildRetrievalLog({ query_text: 'deposit question', result_count: 1, top_score: 0.91 }),
      ),
    })
    renderObservability()

    expect(await screen.findByText('deposit question')).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Retrieval log' })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows a loading state before the first page arrives', () => {
    mockApi({ [LOG_LIST]: logsOf() })
    renderObservability()
    expect(screen.getByTestId('loading-state')).toBeVisible()
  })

  it('offers a retry when the list fails to load', async () => {
    const fetchMock = mockApi({
      [LOG_LIST]: () => json(500, { detail: { code: 'internal_error', message: 'Boom.' } }),
    })
    renderObservability()

    expect(await screen.findByTestId('error-state')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    })
  })

  it('renders the genuinely empty state when there are no logs at all', async () => {
    mockApi({ [LOG_LIST]: logsOf() })
    renderObservability()

    const empty = await screen.findByTestId('empty-state')
    expect(within(empty).getByText('No retrievals logged yet')).toBeVisible()
  })

  it('renders the filtered empty state and offers to clear filters', async () => {
    mockApi({ [LOG_LIST]: logsOf() })
    renderObservability('/observability?q=nonsense')

    const empty = await screen.findByTestId('empty-state')
    expect(within(empty).getByText('No retrievals match your filters')).toBeVisible()
    expect(within(empty).getByRole('button', { name: 'Clear filters' })).toBeVisible()
  })

  it('sends the search box as q and has_results as a boolean query param', async () => {
    const fetchMock = mockApi({ [LOG_LIST]: logsOf(buildRetrievalLog()) })
    renderObservability()
    await screen.findByText('What is the deposit for a viewing?')

    await userEvent.type(screen.getByLabelText('Search'), 'deposit')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => {
      expect(lastQuery(fetchMock, LOG_PATH).get('q')).toBe('deposit')
    })

    await userEvent.selectOptions(screen.getByLabelText('Results'), 'true')
    await waitFor(() => {
      expect(lastQuery(fetchMock, LOG_PATH).get('has_results')).toBe('true')
    })
    // The typed search survives the select change rather than being discarded.
    expect(lastQuery(fetchMock, LOG_PATH).get('q')).toBe('deposit')
  })

  it('renders a log with zero results with a null top_score as a dash, never 0', async () => {
    mockApi({
      [LOG_LIST]: logsOf(buildRetrievalLog({ result_count: 0, top_score: null, results: [] })),
    })
    renderObservability()

    await screen.findByText('What is the deposit for a viewing?')
    const row = screen.getByText('What is the deposit for a viewing?').closest('tr')
    if (!row) throw new Error('no row')
    expect(within(row).getByText('—')).toBeVisible()
  })

  it('expands a row to show the retrieved FAQ entries inline, with no separate fetch', async () => {
    const fetchMock = mockApi({ [LOG_LIST]: logsOf(buildRetrievalLog()) })
    renderObservability()

    await screen.findByText('What is the deposit for a viewing?')
    expect(screen.queryByText('What is the booking deposit?')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Show results/ }))

    const question = await screen.findByText('What is the booking deposit?')
    expect(question).toBeVisible()
    // The retrieved-result table, scoped: the outer log row's own Top score cell also
    // reads "0.910" for this fixture, so an unscoped query would match both.
    const resultsTable = question.closest('table')
    if (!resultsTable) throw new Error('no results table')
    expect(within(resultsTable).getByText('booking')).toBeVisible()
    expect(within(resultsTable).getByText('0.910')).toBeVisible()
    // No GET /retrieval-logs/{id} exists in the contract — the row expands from data it
    // already has.
    expect(fetchMock.mock.calls.filter(([input]) => new URL(input).pathname.includes('/retrieval-logs/'))).toHaveLength(0)

    await userEvent.click(screen.getByRole('button', { name: 'Hide results' }))
    expect(screen.queryByText('What is the booking deposit?')).not.toBeInTheDocument()
  })

  it('surfaces an invalid date range as a fixable error rather than a generic one', async () => {
    mockApi({
      [LOG_LIST]: () =>
        json(422, {
          detail: { code: 'invalid_date_range', message: 'date_from must not be later than date_to.' },
        }),
    })
    renderObservability('/observability?date_from=2026-08-05&date_to=2026-08-01')

    const errorState = await screen.findByTestId('error-state')
    expect(within(errorState).getByText('That date range cannot be searched')).toBeVisible()
    // Scoped: the filters form above also renders its own "Clear filters" ghost button
    // once a filter is active, and both share the same accessible name.
    expect(within(errorState).getByRole('button', { name: 'Clear filters' })).toBeVisible()
  })
})

describe('ObservabilityPage eval runs tab', () => {
  async function openEvalRunsTab() {
    await userEvent.click(screen.getByRole('tab', { name: 'Eval runs' }))
    await screen.findByRole('heading', { name: 'Run history' })
  }

  it('switches tabs without carrying the retrieval log page state over', async () => {
    mockApi({ [LOG_LIST]: logsOf(buildRetrievalLog()), [RUNS_LIST]: runsOf(buildEvalRunSummary()) })
    renderObservability()
    await screen.findByText('What is the deposit for a viewing?')

    await openEvalRunsTab()
    expect(screen.getByRole('tab', { name: 'Eval runs' })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows a loading state, then the run history table', async () => {
    mockApi({
      [LOG_LIST]: logsOf(),
      [RUNS_LIST]: runsOf(buildEvalRunSummary({ id: 'run-42' })),
    })
    renderObservability('/observability?tab=eval-runs')

    expect(await screen.findByRole('table')).toBeVisible()
    // Recall@1: 79.0% from the fixture's recall_at_k.
    expect(screen.getByText('@1: 79.0%')).toBeVisible()
  })

  it('renders a failed run row with dashes, never zeroes, for every metric', async () => {
    mockApi({ [LOG_LIST]: logsOf(), [RUNS_LIST]: runsOf(buildFailedEvalRunSummary()) })
    renderObservability('/observability?tab=eval-runs')

    await screen.findByRole('table')
    const row = screen.getByText('Failed').closest('tr')
    if (!row) throw new Error('no row for the failed run')

    const cells = within(row).getAllByRole('cell')
    // Recall@K, then MRR — the two headline cells.
    expect(cells[3]).toHaveTextContent('—')
    expect(cells[4]).toHaveTextContent('—')
    // The abstention/identity supporting text folded into the MRR cell falls back too.
    expect(cells[4]).toHaveTextContent('Abstention — · Identity Recall@1 —')
    expect(row).not.toHaveTextContent('0.00')
    expect(row).not.toHaveTextContent('0.0%')
  })

  it('triggers a run with the default options and shows the headline cards on success', async () => {
    const fetchMock = mockApi({
      [LOG_LIST]: logsOf(),
      [RUNS_LIST]: runsOf(),
      [RUN_TRIGGER]: () => json(201, buildEvalRunDetail()),
    })
    renderObservability('/observability?tab=eval-runs')
    await screen.findByRole('button', { name: 'Run evaluation' })

    await userEvent.click(screen.getByRole('button', { name: 'Run evaluation' }))

    await waitFor(() => {
      // Every tier stays selected by default, so `tiers` is omitted entirely (the
      // backend's own default is "every tier") — only `k_values` goes on the wire.
      expect(bodyOf(fetchMock, 'POST', RUNS_PATH)).toEqual({ k_values: [1, 3, 5] })
    })

    // The headline cards render Recall@K and MRR prominently after a successful trigger.
    expect(await screen.findByText('Recall@1')).toBeVisible()
    expect(screen.getByText('MRR')).toBeVisible()
  })

  it('disables the button and shows a loading label while the run is in flight', async () => {
    let resolve: (r: Response) => void = () => undefined
    const pending = new Promise<Response>((res) => {
      resolve = res
    })
    const fetchMock = mockApi({
      [LOG_LIST]: logsOf(),
      [RUNS_LIST]: runsOf(),
      [RUN_TRIGGER]: () => pending as unknown as Response,
    })
    renderObservability('/observability?tab=eval-runs')
    await screen.findByRole('button', { name: 'Run evaluation' })

    await userEvent.click(screen.getByRole('button', { name: 'Run evaluation' }))
    expect(screen.getByRole('button', { name: 'Running…' })).toBeDisabled()

    resolve(json(201, buildEvalRunDetail()))
    await screen.findByRole('button', { name: 'Run evaluation' })
    expect(fetchMock).toHaveBeenCalled()
  })

  it('tells the admin to reindex when the FAQ index has no rows', async () => {
    mockApi({
      [LOG_LIST]: logsOf(),
      [RUNS_LIST]: runsOf(),
      [RUN_TRIGGER]: () =>
        json(503, {
          detail: {
            code: 'faq_index_unavailable',
            message: 'The FAQ retrieval index has no rows for the configured embedding model.',
          },
        }),
    })
    renderObservability('/observability?tab=eval-runs')
    await screen.findByRole('button', { name: 'Run evaluation' })

    await userEvent.click(screen.getByRole('button', { name: 'Run evaluation' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Run the FAQ reindex, then try again.')
  })

  it('links to the failed run when the orchestration fails mid-run', async () => {
    mockApi({
      [LOG_LIST]: logsOf(),
      [RUNS_LIST]: runsOf(),
      [RUN_TRIGGER]: () =>
        json(502, {
          detail: { code: 'eval_run_failed', message: 'The evaluation run failed.', run_id: 'run-broke' },
        }),
    })
    renderObservability('/observability?tab=eval-runs')
    await screen.findByRole('button', { name: 'Run evaluation' })

    await userEvent.click(screen.getByRole('button', { name: 'Run evaluation' }))

    const link = await screen.findByRole('link', { name: 'View the failed run' })
    expect(link).toHaveAttribute('href', '/observability/eval-runs/run-broke')
  })

  it('links each history row to its eval run detail page', async () => {
    mockApi({
      [LOG_LIST]: logsOf(),
      [RUNS_LIST]: runsOf(buildEvalRunSummary({ id: 'run-nav' })),
    })
    renderObservability('/observability?tab=eval-runs')

    await screen.findByRole('table')
    const runLink = screen
      .getAllByRole('link')
      .find((el) => el.getAttribute('href') === '/observability/eval-runs/run-nav')
    expect(runLink).toBeDefined()
  })
})

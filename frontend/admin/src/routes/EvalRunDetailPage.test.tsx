import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { json, mockApi } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'
import { buildEvalCase, buildEvalRunDetail } from '@/test/observabilityFixtures'

const DETAIL = (id: string) => `GET /api/v1/observability/retrieval/eval-runs/${id}`

function renderDetail(runId = 'run-1') {
  return renderApp({ user: buildUser('admin'), initialEntries: [`/observability/eval-runs/${runId}`] })
}

describe('EvalRunDetailPage', () => {
  it('renders the headline metrics and the per-case breakdown', async () => {
    mockApi({
      [DETAIL('run-1')]: () =>
        json(
          200,
          buildEvalRunDetail({
            cases: [buildEvalCase({ case_id: 'q-007', query_text: 'deposit amount?' })],
          }),
        ),
    })
    renderDetail('run-1')

    expect(await screen.findByRole('heading', { name: 'Eval run' })).toBeVisible()
    expect(screen.getByText('q-007')).toBeVisible()
    expect(screen.getByText('deposit amount?')).toBeVisible()
    expect(screen.getByText('Recall@1')).toBeVisible()
  })

  it('renders — for a case with no first relevant rank, not 0', async () => {
    mockApi({
      [DETAIL('run-1')]: () =>
        json(
          200,
          buildEvalRunDetail({
            cases: [buildEvalCase({ case_id: 'q-miss', first_relevant_rank: null })],
          }),
        ),
    })
    renderDetail('run-1')

    await screen.findByRole('heading', { name: 'Eval run' })
    const row = screen.getByText('q-miss').closest('tr')
    if (!row) throw new Error('no row for q-miss')
    // "First relevant rank" is the fourth cell; it must read "—", not the literal "0"
    // rank 0 would never be a real value for.
    const cells = within(row).getAllByRole('cell')
    expect(cells[3]).toHaveTextContent('—')
    expect(cells[3]).not.toHaveTextContent('0')
  })

  it('shows a loading state before the run arrives', () => {
    mockApi({ [DETAIL('run-1')]: () => new Promise(() => undefined) as unknown as Response })
    renderDetail('run-1')
    expect(screen.getByTestId('loading-state')).toBeVisible()
  })

  it('renders a not-found state for an unknown run, with no retry offered', async () => {
    mockApi({
      [DETAIL('run-missing')]: () =>
        json(404, { detail: { code: 'eval_run_not_found', message: 'That run does not exist.' } }),
    })
    renderDetail('run-missing')

    expect(await screen.findByTestId('error-state')).toBeVisible()
    expect(screen.getByText('Run not found')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('offers a retry for a genuine failure, distinct from not-found', async () => {
    const fetchMock = mockApi({
      [DETAIL('run-1')]: () => json(500, { detail: { code: 'internal_error', message: 'Boom.' } }),
    })
    renderDetail('run-1')

    expect(await screen.findByTestId('error-state')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    })
  })

  it('links back to the eval runs tab', async () => {
    mockApi({ [DETAIL('run-1')]: () => json(200, buildEvalRunDetail()) })
    renderDetail('run-1')

    expect(await screen.findByRole('link', { name: /Back to eval runs/ })).toHaveAttribute(
      'href',
      '/observability?tab=eval-runs',
    )
  })
})

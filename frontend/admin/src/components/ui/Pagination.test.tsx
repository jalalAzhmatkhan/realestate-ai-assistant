import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Pagination from './Pagination'
import type { PageMeta } from '@/lib/api/pagination'

const meta = (overrides: Partial<PageMeta> = {}): PageMeta => ({
  page: 1,
  page_size: 20,
  total: 100,
  total_pages: 5,
  ...overrides,
})

describe('Pagination', () => {
  it('renders nothing when everything fits on one page', () => {
    const { container } = render(
      <Pagination meta={meta({ total: 3, total_pages: 1 })} onPageChange={vi.fn()} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('reports the range the current page covers', () => {
    render(<Pagination meta={meta({ page: 3 })} onPageChange={vi.fn()} />)

    expect(screen.getByText('Showing 41–60 of 100')).toBeVisible()
  })

  it('clamps the last page to the true total rather than page_size arithmetic', () => {
    render(<Pagination meta={meta({ page: 5, total: 92 })} onPageChange={vi.fn()} />)

    expect(screen.getByText('Showing 81–92 of 92')).toBeVisible()
  })

  it('disables the edges so a page cannot be requested outside the range', () => {
    const { rerender } = render(<Pagination meta={meta()} onPageChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: '← Prev' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next →' })).toBeEnabled()

    rerender(<Pagination meta={meta({ page: 5 })} onPageChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Next →' })).toBeDisabled()
  })

  it('marks the current page for assistive tech', () => {
    render(<Pagination meta={meta({ page: 2 })} onPageChange={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Page 2' })).toHaveAttribute('aria-current', 'page')
  })

  it('collapses distant pages instead of rendering hundreds of buttons', () => {
    render(<Pagination meta={meta({ page: 20, total: 800, total_pages: 40 })} onPageChange={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Page 1' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Page 40' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Page 10' })).not.toBeInTheDocument()
    expect(screen.getAllByText('…')).toHaveLength(2)
  })

  it('reports the requested page', () => {
    const onPageChange = vi.fn()
    render(<Pagination meta={meta({ page: 2 })} onPageChange={onPageChange} />)

    fireEvent.click(screen.getByRole('button', { name: '← Prev' }))
    fireEvent.click(screen.getByRole('button', { name: 'Page 4' }))

    expect(onPageChange).toHaveBeenNthCalledWith(1, 1)
    expect(onPageChange).toHaveBeenNthCalledWith(2, 4)
  })

  it('locks navigation while a page is in flight, so a double click cannot skip one', () => {
    const onPageChange = vi.fn()
    render(<Pagination meta={meta({ page: 2 })} onPageChange={onPageChange} isPending />)

    fireEvent.click(screen.getByRole('button', { name: 'Next →' }))

    expect(onPageChange).not.toHaveBeenCalled()
  })
})

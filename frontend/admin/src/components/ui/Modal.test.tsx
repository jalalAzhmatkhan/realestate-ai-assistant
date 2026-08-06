import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import Button from './Button'
import Modal from './Modal'

function Harness({ onClose = vi.fn() }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <Button
        onClick={() => {
          setOpen(true)
        }}
      >
        Open
      </Button>
      <Modal
        open={open}
        onClose={() => {
          setOpen(false)
          onClose()
        }}
        title="Cancel this viewing?"
        footer={<Button>Confirm</Button>}
      >
        <p>This cannot be undone.</p>
      </Modal>
    </>
  )
}

describe('Modal', () => {
  it('keeps its contents out of the document until it is opened', async () => {
    render(<Harness />)

    // A table renders one of these per row; twenty mounted-but-hidden copies of a
    // booking's details would be twenty things a screen reader can still reach.
    expect(screen.queryByText('This cannot be undone.')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    expect(screen.getByRole('dialog')).toBeVisible()
    expect(screen.getByText('This cannot be undone.')).toBeVisible()
  })

  it('opens as a modal, which is what supplies the focus trap and backdrop', async () => {
    render(<Harness />)

    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    // `showModal()` rather than the `open` attribute: the attribute alone renders a
    // non-modal dialog with no top layer, no backdrop and no trapped focus.
    expect(screen.getByRole('dialog')).toHaveAttribute('open')
  })

  it('routes Escape through React instead of letting the element close behind it', async () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} />)

    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    // What the browser fires on Escape. Letting its default run would close the element
    // while `open` stayed true, leaving the modal unopenable until toggled twice.
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }))

    expect(onClose).toHaveBeenCalledOnce()
    expect(screen.queryByText('This cannot be undone.')).not.toBeInTheDocument()
  })

  it('closes on the backdrop but not on the box itself', async () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} />)

    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    await userEvent.click(screen.getByText('This cannot be undone.'))
    expect(onClose).not.toHaveBeenCalled()

    // The `<dialog>` element is the area around the padded box, so a click landing on it
    // is a click on the backdrop.
    fireEvent.click(screen.getByRole('dialog'))
    expect(onClose).toHaveBeenCalledOnce()
  })
})

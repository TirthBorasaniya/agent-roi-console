import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiContext } from '../../api/client'
import { mockApi } from '../../test/mockApi'
import WorkflowDetail from '../WorkflowDetail'

function renderWith(api = mockApi()) {
  return render(
    <ApiContext.Provider value={api}>
      <MemoryRouter initialEntries={['/workflows/crm_pipeline']}>
        <Routes>
          <Route path="/workflows/:workflowId" element={<WorkflowDetail />} />
        </Routes>
      </MemoryRouter>
    </ApiContext.Provider>,
  )
}

describe('WorkflowDetail', () => {
  it('shows ROI cards, assumptions, and the sensitivity chart', async () => {
    renderWith()
    expect(await screen.findByText('CRM Pipeline Summary')).toBeInTheDocument()
    expect(await screen.findByText('3.10x')).toBeInTheDocument()
    expect(screen.getByText('break-even 66%')).toBeInTheDocument()
    expect(screen.getByText(/Baseline 12 min/)).toHaveTextContent('assumption')
    expect(await screen.findByTestId('sensitivity-chart')).toBeInTheDocument()
  })

  it('expands a run into its step trace and validator results', async () => {
    renderWith()
    fireEvent.click(await screen.findByText('run_abc'))
    expect(await screen.findByText('fetch_opportunities')).toBeInTheDocument()
    expect(screen.getByText(/pass slack_message_posted/)).toBeInTheDocument()
  })

  it('triggers a run and reports the outcome', async () => {
    const triggerRun = vi.fn(async () => ({ run_id: 'run_new', success: true, failure_reason: null }))
    renderWith(mockApi({ triggerRun }))
    fireEvent.click(await screen.findByText('Run one task'))
    expect(await screen.findByText('run_new: success')).toBeInTheDocument()
    expect(triggerRun).toHaveBeenCalledWith('crm_pipeline')
  })

  it('hides the run button on the read-only demo', async () => {
    renderWith(mockApi({ o_read_only: true }))
    await screen.findByText('CRM Pipeline Summary')
    expect(screen.queryByText('Run one task')).not.toBeInTheDocument()
  })
})

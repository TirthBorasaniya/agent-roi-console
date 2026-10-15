import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ApiContext } from '../../api/client'
import { DemoBanner } from '../../components/Layout'
import { mockApi } from '../../test/mockApi'
import Overview from '../Overview'

function renderWith(api = mockApi()) {
  return render(
    <ApiContext.Provider value={api}>
      <MemoryRouter>
        <DemoBanner />
        <Overview />
      </MemoryRouter>
    </ApiContext.Provider>,
  )
}

describe('Overview', () => {
  it('shows net ROI, success with interval, and guard state from the API', async () => {
    renderWith()
    expect(await screen.findByText('CRM Pipeline Summary')).toBeInTheDocument()
    expect(screen.getByText('$42.50')).toBeInTheDocument()
    expect(screen.getByText('-$310.20')).toBeInTheDocument()
    expect(screen.getByText('90% (60% to 98%)')).toBeInTheDocument()
    expect(screen.getByText('paused')).toBeInTheDocument()
    expect(screen.getByText('baseline assumed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'CRM Pipeline Summary' })).toHaveAttribute('href', '/workflows/crm_pipeline')
  })

  it('totals net ROI across workflows', async () => {
    renderWith()
    expect(await screen.findByText('-$267.70')).toBeInTheDocument()
  })

  it('shows the demo banner when the API reports demo data', async () => {
    renderWith()
    expect(await screen.findByRole('status')).toHaveTextContent('Demo data')
  })

  it('reports load errors', async () => {
    renderWith(mockApi({ overview: async () => Promise.reject(new Error('boom')) }))
    expect(await screen.findByText(/Failed to load overview: boom/)).toBeInTheDocument()
  })
})

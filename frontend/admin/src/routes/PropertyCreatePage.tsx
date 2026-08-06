import { Link, useNavigate } from 'react-router'

import PropertyForm from '@/components/properties/PropertyForm'
import { useCreateProperty } from '@/lib/properties/mutations'

/**
 * Its own route and its own component, not the edit screen holding a magic `new` id.
 * Sharing that component would mean `/properties/new` firing
 * `GET /api/v1/properties/new` on mount — a 404 for a screen that has nothing to fetch.
 * This one issues no read at all.
 */
export default function PropertyCreatePage() {
  const navigate = useNavigate()
  const create = useCreateProperty()

  return (
    <div className="space-y-4">
      <Link
        to="/properties"
        className="inline-block text-sm text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        ← Back to properties
      </Link>

      <h1 className="text-xl font-semibold text-slate-900">New property</h1>

      <PropertyForm
        submitLabel="Create property"
        isSubmitting={create.isPending}
        error={create.error}
        onSubmit={(payload) => {
          create.mutate(payload, {
            // `replace`: Back from the new listing should return to the list, not to a
            // create form whose submission already succeeded.
            onSuccess: (property) => void navigate(`/properties/${property.id}`, { replace: true }),
          })
        }}
        onCancel={() => {
          void navigate('/properties')
        }}
      />
    </div>
  )
}

import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'

import ConfirmDeactivateButton from '@/components/properties/ConfirmDeactivateButton'
import PropertyForm from '@/components/properties/PropertyForm'
import PropertyReadOnlyView from '@/components/properties/PropertyReadOnlyView'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import StatusBadge from '@/components/ui/StatusBadge'
import { isApiError } from '@/lib/api/errors'
import { useCurrentUser } from '@/lib/auth/useCurrentUser'
import { useDeactivateProperty, useUpdateProperty } from '@/lib/properties/mutations'
import { canEditProperty } from '@/lib/properties/permissions'
import { propertyDetailQueryOptions } from '@/lib/properties/queries'
import type { PropertyDetail } from '@/lib/properties/types'

export default function PropertyDetailPage() {
  const { propertyId = '' } = useParams()
  const { data: user } = useCurrentUser()

  // Always the single-item GET, never a row lifted out of the list cache: the list shape
  // has no `latitude`, `longitude`, `amenities`, `description` or `listed_date`, all of
  // which the full-replacement PUT requires.
  const {
    data: property,
    isPending,
    isError,
    error,
    refetch,
  } = useQuery(propertyDetailQueryOptions(propertyId))

  if (isPending || !user) {
    return <LoadingState label="Loading property…" rows={6} />
  }

  if (isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        {isNotFound(error) ? (
          // No retry offered: a 404 here is a settled answer, and a button that will
          // re-fetch the same 404 only invites the user to keep pressing it.
          <ErrorState title="Not found or not accessible" error={error} />
        ) : (
          <ErrorState
            title="Could not load this property"
            error={error}
            onRetry={() => {
              void refetch()
            }}
          />
        )}
      </div>
    )
  }

  return <PropertyDetailView property={property} canEdit={canEditProperty(user, property)} />
}

function PropertyDetailView({
  property,
  canEdit,
}: {
  property: PropertyDetail
  canEdit: boolean
}) {
  const navigate = useNavigate()
  const update = useUpdateProperty(property.id)
  const deactivate = useDeactivateProperty(property.id)

  return (
    <div className="space-y-4">
      <BackLink />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">{property.title}</h1>
        <StatusBadge status={property.status} />
      </div>

      {deactivate.isError ? (
        <p
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {isNotFound(deactivate.error)
            ? 'This listing is no longer yours to change.'
            : 'Could not deactivate this listing. Please try again.'}
        </p>
      ) : null}

      {update.isSuccess && !update.isPending ? (
        <p
          role="status"
          className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800"
        >
          Changes saved.
        </p>
      ) : null}

      {canEdit ? (
        <PropertyForm
          // Rebuilds the form whenever the server's status changes underneath it.
          // Deactivating is the one write that moves this record without the form having
          // submitted it, and it moves exactly this field — leaving the stale draft in
          // place would make the next Save resubmit "active" and undo the deactivation.
          // Keyed on the record rather than on a counter deliberately: a counter bumped
          // from the mutation callback renders one tick *before* the query cache lands,
          // so the form would rebuild from the record it was already showing.
          key={property.status}
          initial={property}
          submitLabel="Save"
          isSubmitting={update.isPending}
          error={update.error}
          onSubmit={(payload) => {
            update.mutate(payload)
          }}
          onCancel={() => {
            void navigate('/properties')
          }}
          secondaryActions={
            // Nothing to deactivate on a listing already in `draft` — the badge above
            // already says so, and a disabled button explains less than its absence.
            property.status === 'draft' ? null : (
              <ConfirmDeactivateButton
                isPending={deactivate.isPending}
                onConfirm={() => {
                  deactivate.mutate()
                }}
              />
            )
          }
        />
      ) : (
        <PropertyReadOnlyView property={property} />
      )}
    </div>
  )
}

function BackLink() {
  return (
    <Link
      to="/properties"
      className="inline-block text-sm text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
    >
      ← Back to properties
    </Link>
  )
}

/**
 * `404 property_not_found` is the backend's answer for both an unknown id and one
 * outside the caller's visibility scope — deliberately indistinguishable, so this screen
 * must not word it as "deleted" or as "forbidden" either.
 */
function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.status === 404
}

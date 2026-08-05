import { Link } from 'react-router'

export default function NotFoundPage() {
  return (
    <main className="p-6">
      <h1 className="text-xl font-semibold text-slate-900">Page not found</h1>
      <Link className="mt-2 inline-block text-sm text-blue-600 underline" to="/">
        Back to the dashboard
      </Link>
    </main>
  )
}

import './styles/App.css'
import React, { Suspense, lazy } from 'react'

const Dashboard = lazy(() =>
  import('./components/Dashboard').then((module) => ({ default: module.Dashboard })),
)

type ErrorBoundaryState = {
  hasError: boolean
  message: string
}

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      message: error?.stack || error?.message || 'Unknown render error',
    }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('React render crash:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="auth-shell">
          <section className="auth-card">
            <span className="eyebrow">Executive Advisor</span>
            <h1>UI crashed during render</h1>
            <p className="auth-copy">
              The assistant shell hit a frontend error. The backend session is unchanged.
            </p>
            <pre className="ui-crash-copy">{this.state.message}</pre>
          </section>
        </div>
      )
    }
    return this.props.children
  }
}

function App() {
  return (
    <ErrorBoundary>
      <div className="App">
        <Suspense
          fallback={
            <div className="auth-shell">
              <section className="auth-card">
                <span className="eyebrow">Executive Advisor</span>
                <h1>Loading dashboard</h1>
                <p className="auth-copy">Preparing the assistant workspace…</p>
              </section>
            </div>
          }
        >
          <Dashboard />
        </Suspense>
      </div>
    </ErrorBoundary>
  )
}

export default App

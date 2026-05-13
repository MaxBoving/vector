import React from 'react'

type DashboardShellProps = {
  sidebar: React.ReactNode
  main: React.ReactNode
}

export const DashboardShell: React.FC<DashboardShellProps> = ({ sidebar, main }) => (
  <div className="ceo-shell">
    <aside className="ceo-sidebar">{sidebar}</aside>
    <main className="ceo-main">{main}</main>
  </div>
)

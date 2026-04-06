import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Overview } from './pages/Overview'
import { Topology } from './pages/Topology'
import { Logs } from './pages/Logs'
import { Chat } from './pages/Chat'
import { Config } from './pages/Config'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/topology" element={<Topology />} />
          <Route path="/topology/:runId" element={<Topology />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/logs/:runId" element={<Logs />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/config" element={<Config />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

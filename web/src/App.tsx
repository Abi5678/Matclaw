import { BrowserRouter, Routes, Route } from 'react-router-dom'
import ControlPlane from './pages/ControlPlane'
import VisionGallery from './pages/VisionGallery'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ControlPlane />} />
        <Route path="/vision" element={<VisionGallery />} />
      </Routes>
    </BrowserRouter>
  )
}

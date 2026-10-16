import type { Metadata, Viewport } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: 'IntelligenceGrid — open global situational awareness',
  description:
    'Fourteen analysis modules over free, key-less public sources, with explicit ' +
    'coverage confidence so a quiet region reads as low-data rather than as calm.',
  applicationName: 'IntelligenceGrid',
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: '#070b11',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}

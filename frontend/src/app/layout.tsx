import type { Metadata, Viewport } from "next";

import "./globals.css";
import { Providers } from "@/components/layout/providers";

export const metadata: Metadata = {
  title: { default: "AVINASH · BowserAuto", template: "%s · AVINASH" },
  description: "AVINASH — a JARVIS-style AI browser automation workspace: persistent browser sessions, task agent, live viewer and human takeover.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = { themeColor: "#00060a", width: "device-width", initialScale: 1 };

// Applies theme + accent before paint to avoid a flash of the default palette.
const themeScript = `(function(){try{var t=localStorage.getItem('ba.theme');if(t!=='light'){t='dark'}document.documentElement.classList.toggle('dark',t==='dark');var a=localStorage.getItem('ba.accent');if(a&&/^#[0-9a-f]{6}$/i.test(a)){var r=parseInt(a.slice(1,3),16)/255,g=parseInt(a.slice(3,5),16)/255,b=parseInt(a.slice(5,7),16)/255;var mx=Math.max(r,g,b),mn=Math.min(r,g,b),d=mx-mn,h=0,l=(mx+mn)/2,s=d?(l>0.5?d/(2-mx-mn):d/(mx+mn)):0;if(d){if(mx===r){h=((g-b)/d+(g<b?6:0))*60}else if(mx===g){h=((b-r)/d+2)*60}else{h=((r-g)/d+4)*60}}var st=document.documentElement.style;st.setProperty('--hue',String(s<0.08?190:Math.round(h)));st.setProperty('--sat',s<0.08?'0.15':'1');document.documentElement.dataset.accent=a.toLowerCase()}}catch(e){document.documentElement.classList.add('dark')}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

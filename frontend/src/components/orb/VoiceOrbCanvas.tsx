"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { prefersReducedMotion } from "@/lib/anim";
import { FRAGMENT, VERTEX } from "./orb-shaders";
import { ORB_COLOR, type VoiceOrbProps } from "./types";

/** Minimal voice-activity orb: one smooth, flat-coloured sphere that swells gently with the audio level. */
export default function VoiceOrbCanvas({ state, level = 0, getLevel, className, label }: VoiceOrbProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  // Latest props for the render loop without re-creating the scene.
  const live = useRef({ state, level, getLevel });
  live.current = { state, level, getLevel };
  const requestRender = useRef<() => void>(() => {});

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
    } catch {
      setFailed(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const canvas = renderer.domElement;
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.setAttribute("aria-hidden", "true");
    host.appendChild(canvas);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
    camera.position.set(0, 0, 4.8);

    const geometry = new THREE.IcosahedronGeometry(1, 24);
    const color = new THREE.Color(ORB_COLOR[live.current.state] ?? ORB_COLOR.idle);
    const target = new THREE.Color();
    const uniforms = { uTime: { value: 0 }, uLevel: { value: 0 }, uColor: { value: color } };
    const material = new THREE.ShaderMaterial({ vertexShader: VERTEX, fragmentShader: FRAGMENT, uniforms });
    const orb = new THREE.Mesh(geometry, material);
    scene.add(orb);

    const reduced = prefersReducedMotion();
    let raf = 0;
    let running = false;
    let visible = true;
    let inView = true;
    let smoothed = 0;
    let last = performance.now();
    let elapsed = 0;

    function resize() {
      const w = Math.max(1, host!.clientWidth);
      const h = Math.max(1, host!.clientHeight);
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.position.z = w < h ? 4.8 * (h / w) : 4.8;
      camera.updateProjectionMatrix();
    }

    function readLevel() {
      const { level: lv, getLevel: gl } = live.current;
      return Math.max(0, Math.min(1, gl ? gl() : lv));
    }

    function frame(now: number) {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      elapsed += dt;
      const t = readLevel();
      smoothed += (t - smoothed) * (t > smoothed ? 0.25 : 0.06);
      uniforms.uTime.value = elapsed;
      uniforms.uLevel.value = smoothed;
      orb.scale.setScalar(1 + smoothed * 0.06);
      target.set(ORB_COLOR[live.current.state] ?? ORB_COLOR.idle);
      color.lerp(target, Math.min(1, dt * 10));
      renderer.render(scene, camera);
      raf = requestAnimationFrame(frame);
    }

    function renderStatic() {
      color.set(ORB_COLOR[live.current.state] ?? ORB_COLOR.idle);
      uniforms.uTime.value = 1.3;
      uniforms.uLevel.value = 0;
      renderer.render(scene, camera);
    }

    function updateLoop() {
      const shouldRun = !reduced && visible && inView;
      if (shouldRun && !running) {
        running = true;
        last = performance.now();
        raf = requestAnimationFrame(frame);
      } else if (!shouldRun && running) {
        running = false;
        cancelAnimationFrame(raf);
      }
    }

    resize();
    renderStatic();
    requestRender.current = () => {
      if (!running) renderStatic();
    };

    const ro = new ResizeObserver(() => {
      resize();
      if (!running) renderStatic();
    });
    ro.observe(host);
    const io = new IntersectionObserver((entries) => {
      inView = entries.some((e) => e.isIntersecting);
      updateLoop();
    });
    io.observe(host);
    const onVisibility = () => {
      visible = document.visibilityState === "visible";
      updateLoop();
    };
    document.addEventListener("visibilitychange", onVisibility);
    updateLoop();

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      requestRender.current = () => {};
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
    };
  }, []);

  // Reduced-motion (or paused) mode re-renders on state change.
  useEffect(() => {
    requestRender.current();
  }, [state]);

  return (
    <div ref={hostRef} className={className} role="img" aria-label={label ?? `Voice activity: ${state}`}>
      {failed ? (
        <div className="mx-auto aspect-square h-full max-h-full rounded-full" style={{ background: ORB_COLOR[state] }} />
      ) : null}
    </div>
  );
}

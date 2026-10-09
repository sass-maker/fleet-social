import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';

type Direction = 'cel' | 'diagram' | 'atmospheric' | 'ascii' | 'type';

const DIRECTIONS: Array<{
  id: Direction;
  name: string;
  short: string;
}> = [
  { id: 'cel', name: 'Cel Orbit', short: 'WebGL · outlined geometry' },
  { id: 'diagram', name: 'Signal Diagram', short: 'WebGL · connected nodes' },
  { id: 'atmospheric', name: 'Light Corridor', short: 'WebGL · camera flight' },
  { id: 'ascii', name: 'ASCII Signal', short: 'Canvas · glyph field' },
  { id: 'type', name: 'Kinetic Type', short: 'Canvas · editorial motion' },
];

const LOOP_MS = 10_000;

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReduced(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);
  return reduced;
}

function ThreeRangeScene({ variant, reduced }: { variant: 'diagram' | 'atmospheric'; reduced: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = variant === 'diagram' ? 1.15 : 1.35;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 90);
    const animated: THREE.Object3D[] = [];
    const materials: THREE.Material[] = [];
    const geometries: THREE.BufferGeometry[] = [];

    if (variant === 'diagram') {
      scene.background = new THREE.Color('#06101d');
      scene.fog = new THREE.Fog('#06101d', 10, 24);
      camera.position.set(0, 0.6, 11);
      const group = new THREE.Group();
      scene.add(group);
      animated.push(group);

      const coreGeometry = new THREE.TorusKnotGeometry(1.2, 0.34, 128, 18);
      const coreMaterial = new THREE.MeshStandardMaterial({ color: '#f4c45e', metalness: 0.48, roughness: 0.22 });
      geometries.push(coreGeometry);
      materials.push(coreMaterial);
      const core = new THREE.Mesh(coreGeometry, coreMaterial);
      group.add(core);
      animated.push(core);

      const nodePositions = [
        [-3.6, 2.7, -1.2], [3.8, 2.1, -2.4], [-4.2, -2.0, -3.1], [3.4, -2.8, -1.0],
        [0.1, 4.1, -4.5], [0.3, -4.0, -3.8], [-5.3, 0.1, -5.2], [5.2, -0.1, -4.8],
      ].map(([x, y, z]) => new THREE.Vector3(x, y, z));
      const nodeGeometry = new THREE.IcosahedronGeometry(0.42, 1);
      const nodeMaterial = new THREE.MeshStandardMaterial({ color: '#64d6ff', emissive: '#0a4261', roughness: 0.35 });
      geometries.push(nodeGeometry);
      materials.push(nodeMaterial);
      for (const [index, position] of nodePositions.entries()) {
        const node = new THREE.Mesh(nodeGeometry, nodeMaterial);
        node.position.copy(position);
        node.userData.phase = index * 0.71;
        node.userData.baseY = position.y;
        group.add(node);
        animated.push(node);
      }
      const lineGeometry = new THREE.BufferGeometry().setFromPoints(nodePositions.flatMap((position) => [position, new THREE.Vector3(0, 0, 0)]));
      const lineMaterial = new THREE.LineBasicMaterial({ color: '#2f7fa1', transparent: true, opacity: 0.58 });
      geometries.push(lineGeometry);
      materials.push(lineMaterial);
      group.add(new THREE.LineSegments(lineGeometry, lineMaterial));
      const key = new THREE.PointLight('#f4c45e', 38, 18, 2);
      key.position.set(2, 3, 6);
      scene.add(key, new THREE.AmbientLight('#80b9d2', 1.8));
    } else {
      scene.background = new THREE.Color('#09090d');
      scene.fog = new THREE.FogExp2('#09090d', 0.075);
      camera.position.set(0, 0, 13);
      const corridor = new THREE.Group();
      scene.add(corridor);
      animated.push(corridor);
      const colors = ['#f36b45', '#f1c56b', '#74d8ca'];
      for (let index = 0; index < 22; index += 1) {
        const geometry = new THREE.TorusGeometry(2.6 + (index % 3) * 0.38, 0.055 + (index % 2) * 0.025, 8, 48);
        const material = new THREE.MeshBasicMaterial({ color: colors[index % colors.length], transparent: true, opacity: 0.2 + (index % 4) * 0.08 });
        geometries.push(geometry);
        materials.push(material);
        const ring = new THREE.Mesh(geometry, material);
        ring.position.z = 9 - index * 1.45;
        ring.rotation.set(index * 0.11, index * 0.17, index * 0.23);
        corridor.add(ring);
        animated.push(ring);
      }
      const particleGeometry = new THREE.BufferGeometry();
      const positions = new Float32Array(600 * 3);
      for (let index = 0; index < 600; index += 1) {
        positions[index * 3] = (Math.sin(index * 12.989) * 0.5) * 12;
        positions[index * 3 + 1] = (Math.sin(index * 78.233) * 0.5) * 18;
        positions[index * 3 + 2] = 10 - (index % 100) * 0.42;
      }
      particleGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      const particleMaterial = new THREE.PointsMaterial({ color: '#f4ead2', size: 0.035, transparent: true, opacity: 0.66 });
      geometries.push(particleGeometry);
      materials.push(particleMaterial);
      corridor.add(new THREE.Points(particleGeometry, particleMaterial));
    }

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      renderer.setSize(Math.max(1, rect.width), Math.max(1, rect.height), false);
      camera.aspect = Math.max(1, rect.width) / Math.max(1, rect.height);
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const started = performance.now();
    let frame = 0;
    const renderFrame = (now: number) => {
      const cycle = reduced ? 0.42 : ((now - started) % LOOP_MS) / LOOP_MS;
      if (variant === 'diagram') {
        const group = animated[0];
        group.rotation.y = cycle * Math.PI * 2;
        group.rotation.x = Math.sin(cycle * Math.PI * 2) * 0.16;
        const core = animated[1];
        core.rotation.x = cycle * Math.PI * 1.7;
        core.rotation.z = cycle * Math.PI * 1.2;
        for (const node of animated.slice(2)) {
          node.position.y = Number(node.userData.baseY ?? 0)
            + Math.sin(cycle * Math.PI * 2 + Number(node.userData.phase ?? 0)) * 0.18;
        }
        camera.position.z = 11 - Math.sin(cycle * Math.PI) * 2.4;
        camera.lookAt(0, 0, -1.4);
      } else {
        const corridor = animated[0];
        corridor.rotation.z = Math.sin(cycle * Math.PI * 2) * 0.22;
        camera.position.z = 13 - cycle * 18;
        camera.position.x = Math.sin(cycle * Math.PI * 2) * 0.65;
        camera.position.y = Math.cos(cycle * Math.PI * 2) * 0.42;
        camera.lookAt(camera.position.x * 0.25, camera.position.y * 0.25, camera.position.z - 8);
      }
      renderer.render(scene, camera);
      if (!reduced) frame = requestAnimationFrame(renderFrame);
    };
    renderFrame(started);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.dispose();
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
    };
  }, [reduced, variant]);

  return (
    <canvas
      ref={canvasRef}
      className="probe-canvas"
      role="img"
      aria-label={variant === 'diagram'
        ? 'Three-dimensional signal diagram with connected nodes and an orbiting camera'
        : 'Three-dimensional luminous corridor with a camera flying through layered rings'}
    />
  );
}

function addOutlinedMesh(
  group: THREE.Group,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  outline: THREE.Material,
  scale = 1.045,
): THREE.Mesh {
  const mesh = new THREE.Mesh(geometry, material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  group.add(mesh);
  const edge = new THREE.Mesh(geometry, outline);
  edge.scale.setScalar(scale);
  group.add(edge);
  return mesh;
}

function CelOrbit({ reduced }: { reduced: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#dca865');
    scene.fog = new THREE.Fog('#dca865', 18, 34);

    const camera = new THREE.OrthographicCamera(-9, 9, 5, -5, 0.1, 100);
    camera.position.set(10, 9, 13);
    camera.lookAt(0, 0.7, 0);

    const gradient = new THREE.DataTexture(
      new Uint8Array([40, 128, 220, 255]),
      4,
      1,
      THREE.RedFormat,
    );
    gradient.needsUpdate = true;
    gradient.magFilter = THREE.NearestFilter;
    gradient.minFilter = THREE.NearestFilter;

    const toon = (color: string) =>
      new THREE.MeshToonMaterial({ color, gradientMap: gradient });
    const ink = new THREE.MeshBasicMaterial({
      color: '#242027',
      side: THREE.BackSide,
    });

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(46, 30),
      toon('#dca865'),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.08;
    ground.receiveShadow = true;
    scene.add(ground);

    const ambient = new THREE.HemisphereLight('#ffe6a5', '#6e3b36', 1.75);
    scene.add(ambient);
    const sun = new THREE.DirectionalLight('#fff1bf', 4.8);
    sun.position.set(-7, 12, 8);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -16;
    sun.shadow.camera.right = 16;
    sun.shadow.camera.top = 12;
    sun.shadow.camera.bottom = -12;
    scene.add(sun);

    const sunDisc = new THREE.Mesh(
      new THREE.CircleGeometry(2.35, 32),
      new THREE.MeshBasicMaterial({ color: '#ffd46b' }),
    );
    sunDisc.position.set(-6.8, 6.8, -8);
    scene.add(sunDisc);

    const world = new THREE.Group();
    scene.add(world);

    const railMaterial = toon('#643e54');
    for (const z of [-1.55, 1.55]) {
      const rail = new THREE.Group();
      const railMesh = addOutlinedMesh(
        rail,
        new THREE.BoxGeometry(18, 0.22, 0.22),
        railMaterial,
        ink,
        1.07,
      );
      railMesh.position.y = 0.18;
      rail.position.z = z;
      world.add(rail);
    }

    const markers: THREE.Group[] = [];
    for (let i = 0; i < 7; i += 1) {
      const marker = new THREE.Group();
      addOutlinedMesh(
        marker,
        new THREE.CylinderGeometry(0.32, 0.55, 1.3 + (i % 3) * 0.35, 6),
        toon(i % 2 ? '#76465d' : '#ef6546'),
        ink,
        1.065,
      );
      marker.position.set(-7.5 + i * 2.55, 0.68, i % 2 ? 2.7 : -2.7);
      marker.rotation.y = i * 0.37;
      markers.push(marker);
      world.add(marker);
    }

    for (let i = 0; i < 8; i += 1) {
      const rock = new THREE.Group();
      addOutlinedMesh(
        rock,
        new THREE.DodecahedronGeometry(0.35 + (i % 3) * 0.16, 0),
        toon(i % 2 ? '#b86a50' : '#8f574d'),
        ink,
        1.08,
      );
      rock.position.set(-8 + ((i * 3.1) % 17), 0.2, -4 + ((i * 2.7) % 8));
      rock.rotation.set(i * 0.17, i * 0.41, 0);
      world.add(rock);
    }

    const ball = new THREE.Group();
    addOutlinedMesh(
      ball,
      new THREE.IcosahedronGeometry(1.18, 3),
      toon('#ed5138'),
      ink,
      1.055,
    );
    const band = new THREE.Mesh(
      new THREE.TorusGeometry(1.2, 0.07, 8, 48),
      toon('#ffd56f'),
    );
    band.rotation.x = Math.PI / 2;
    ball.add(band);
    world.add(ball);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      renderer.setSize(width, height, false);
      const aspect = width / height;
      const span = aspect < 1 ? 7.2 : 5.4;
      travelSpan = Math.max(2.6, Math.min(8.2, span * aspect - 1.45));
      camera.left = -span * aspect;
      camera.right = span * aspect;
      camera.top = span;
      camera.bottom = -span;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    let travelSpan = 8.2;
    observer.observe(canvas);
    resize();

    const started = performance.now();
    let frame = 0;
    const renderFrame = (now: number) => {
      const cycle = reduced ? 0.42 : ((now - started) % LOOP_MS) / LOOP_MS;
      const travel = cycle * 2 - 1;
      ball.position.set(travel * travelSpan, 1.24 + Math.sin(cycle * Math.PI * 4) * 0.12, 0);
      ball.rotation.z = -travel * Math.PI * 3.2;
      ball.rotation.x = cycle * Math.PI * 2;
      world.rotation.y = Math.sin(cycle * Math.PI * 2) * 0.035;
      markers.forEach((marker, index) => {
        marker.rotation.y = cycle * Math.PI * 0.6 + index * 0.37;
      });
      camera.position.x = 10 + Math.sin(cycle * Math.PI * 2) * 0.7;
      camera.lookAt(ball.position.x * 0.1, 0.65, 0);
      renderer.render(scene, camera);
      if (!reduced) frame = requestAnimationFrame(renderFrame);
    };
    renderFrame(started);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const materials = Array.isArray(object.material)
            ? object.material
            : [object.material];
          materials.forEach((material) => material.dispose());
        }
      });
      gradient.dispose();
    };
  }, [reduced]);

  return (
    <canvas
      ref={canvasRef}
      className="probe-canvas"
      role="img"
      aria-label="Cel-shaded red ball rolling through a warm, outlined low-poly landscape"
    />
  );
}

function useCanvasLoop(
  draw: (context: CanvasRenderingContext2D, width: number, height: number, t: number) => void,
  reduced: boolean,
) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    const started = performance.now();
    let width = 1;
    let height = 1;
    let frame = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio, 2);
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const tick = (now: number) => {
      const t = reduced ? 0.42 : ((now - started) % LOOP_MS) / LOOP_MS;
      draw(context, width, height, t);
      if (!reduced) frame = requestAnimationFrame(tick);
    };
    tick(started);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [draw, reduced]);

  return canvasRef;
}

function drawAscii(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  t: number,
) {
  context.fillStyle = '#080b0a';
  context.fillRect(0, 0, width, height);

  const columns = Math.max(28, Math.min(82, Math.floor(width / 15)));
  const rows = Math.max(22, Math.min(48, Math.floor(height / 17)));
  const cellW = width / columns;
  const cellH = height / rows;
  const signalX = width * (-0.08 + t * 1.16);
  const signalY = height * (0.52 + Math.sin(t * Math.PI * 2) * 0.11);
  const glyphs = ' .·:+*#@';
  const fontSize = Math.max(9, Math.min(15, cellW * 0.82));

  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const x = (column + 0.5) * cellW;
      const y = (row + 0.5) * cellH;
      const wave = Math.sin(column * 0.42 + t * Math.PI * 8) * height * 0.08;
      const distance = Math.hypot(x - signalX, y - signalY - wave);
      const pulse = Math.max(0, 1 - distance / Math.max(width, height) * 3.4);
      const noise = (Math.sin(column * 17.13 + row * 9.71) + 1) * 0.5;
      const value = Math.min(glyphs.length - 1, Math.floor((pulse * 0.78 + noise * 0.2) * glyphs.length));
      context.fillStyle =
        pulse > 0.62 ? '#d7ff43' : pulse > 0.3 ? '#f3f0dd' : '#315545';
      context.fillText(glyphs[value], x, y);
    }
  }

  const phrases = ['CONVICTION', 'USE THE PRODUCT', 'STAY CURIOUS'];
  const phrase = phrases[Math.min(2, Math.floor(t * 3))];
  const display = Math.max(34, Math.min(88, width * 0.075));
  context.textAlign = 'left';
  context.font = `900 ${display}px Arial, Helvetica, sans-serif`;
  context.fillStyle = '#f3f0dd';
  context.fillText(phrase, width * 0.06, height * 0.18);
  context.fillStyle = '#d7ff43';
  context.fillRect(width * 0.06, height * 0.22, Math.max(70, display * 1.8), 5);

  context.strokeStyle = '#d7ff43';
  context.lineWidth = Math.max(2, width / 500);
  context.beginPath();
  context.arc(signalX, signalY, Math.max(18, width * 0.025), 0, Math.PI * 2);
  context.stroke();
}

function AsciiSignal({ reduced }: { reduced: boolean }) {
  const draw = useRef(drawAscii).current;
  const canvasRef = useCanvasLoop(draw, reduced);
  return (
    <canvas
      ref={canvasRef}
      className="probe-canvas"
      role="img"
      aria-label="Animated ASCII signal wave carrying key phrases from the podcast"
    />
  );
}

function drawKineticType(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  t: number,
) {
  context.fillStyle = '#f1e8d0';
  context.fillRect(0, 0, width, height);

  const ink = '#11131a';
  const red = '#e64b36';
  const blue = '#2349d8';
  const ballX = width * (0.04 + t * 0.92);
  const ballY = height * 0.66;
  const radius = Math.max(34, Math.min(82, width * 0.065));

  context.fillStyle = blue;
  context.fillRect(0, 0, width * 0.035, height);
  context.fillStyle = ink;
  context.fillRect(width * 0.72, 0, width * 0.012, height);

  const phase = Math.min(2, Math.floor(t * 3));
  const words = [
    ['CONVICTION', 'STOICISM'],
    ['BUILD', 'FOR PEOPLE'],
    ['BELIEVE', 'BEYOND MONEY'],
  ][phase];
  const proximity = Math.max(0, 1 - Math.abs(ballX - width * 0.5) / (width * 0.42));
  const shove = proximity * width * 0.055;
  const big = Math.max(54, Math.min(150, width * 0.13));
  const small = big * 0.47;

  context.save();
  context.translate(width * 0.09 + shove, height * 0.29);
  context.rotate(-0.025 + proximity * 0.035);
  context.textAlign = 'left';
  context.textBaseline = 'alphabetic';
  context.font = `900 ${big}px Arial Black, Arial, sans-serif`;
  context.fillStyle = ink;
  context.fillText(words[0], 0, 0);
  context.font = `900 ${small}px Arial Black, Arial, sans-serif`;
  context.fillStyle = red;
  context.fillText(words[1], width * 0.018, big * 0.72);
  context.restore();

  context.save();
  context.translate(ballX, ballY);
  context.rotate(t * Math.PI * 7);
  context.fillStyle = red;
  context.strokeStyle = ink;
  context.lineWidth = Math.max(5, radius * 0.1);
  context.beginPath();
  context.arc(0, 0, radius, 0, Math.PI * 2);
  context.fill();
  context.stroke();
  context.beginPath();
  context.moveTo(-radius * 0.76, 0);
  context.lineTo(radius * 0.76, 0);
  context.moveTo(0, -radius * 0.76);
  context.lineTo(0, radius * 0.76);
  context.stroke();
  context.restore();

  context.fillStyle = ink;
  context.font = `700 ${Math.max(12, width * 0.012)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  context.fillText('QUESTION → CONVICTION → PRACTICE', width * 0.075, height * 0.9);
}

function KineticType({ reduced }: { reduced: boolean }) {
  const draw = useRef(drawKineticType).current;
  const canvasRef = useCanvasLoop(draw, reduced);
  return (
    <canvas
      ref={canvasRef}
      className="probe-canvas"
      role="img"
      aria-label="Animated editorial typography pushed across the frame by a rolling red ball"
    />
  );
}

export default function VisualLab() {
  const [active, setActive] = useState<Direction>('cel');
  const [captureMode, setCaptureMode] = useState(false);
  const reduced = useReducedMotion();
  const current = DIRECTIONS.find((direction) => direction.id === active) ?? DIRECTIONS[0];

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedDirection = params.get('direction');
    if (DIRECTIONS.some((direction) => direction.id === requestedDirection)) setActive(requestedDirection as Direction);
    setCaptureMode(params.get('capture') === '1');
  }, []);

  return (
    <main className={`visual-lab${captureMode ? ' visual-lab--capture' : ''}`} data-direction={active}>
      <header className="lab-header">
        <div className="lab-title">
          <span className="lab-mark" aria-hidden="true">Z</span>
          <div>
            <p>ZEROPOD · VISUAL SYSTEM TEST</p>
            <h1>{current.name}</h1>
          </div>
        </div>
        <nav className="direction-tabs" aria-label="Visual directions">
          {DIRECTIONS.map((direction) => (
            <button
              key={direction.id}
              type="button"
              aria-pressed={active === direction.id}
              onClick={() => setActive(direction.id)}
            >
              <span>{direction.name}</span>
              <small>{direction.short}</small>
            </button>
          ))}
        </nav>
      </header>

      <section className="probe-stage" aria-live="polite">
        {active === 'cel' && <CelOrbit reduced={reduced} />}
        {active === 'diagram' && <ThreeRangeScene variant="diagram" reduced={reduced} />}
        {active === 'atmospheric' && <ThreeRangeScene variant="atmospheric" reduced={reduced} />}
        {active === 'ascii' && <AsciiSignal reduced={reduced} />}
        {active === 'type' && <KineticType reduced={reduced} />}
        <div className="stage-index" aria-hidden="true">
          {String(DIRECTIONS.findIndex((direction) => direction.id === active) + 1).padStart(2, '0')}
          <span>/ 05</span>
        </div>
      </section>

      <footer className="lab-footer">
        <div className="source-lockup">
          <span>FROM THE SOURCE</span>
          <strong>01 BELIEVE IN SOMETHING</strong>
          <small>00:56:36 — 00:57:22</small>
        </div>
        <blockquote>
          “Find something that you believe in more than just making money.”
        </blockquote>
        <div className="motion-status">
          <span className="status-dot" aria-hidden="true" />
          {reduced ? 'Reduced motion' : '10s live loop'}
        </div>
      </footer>
    </main>
  );
}

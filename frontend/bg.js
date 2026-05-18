// ═══════════════════════════════════════
// PULSΞ Generative Background
// WebGL wireframe icosahedron with Perlin noise displacement.
// Replaces the heavier Unicorn Studio scene.
// ═══════════════════════════════════════

(function () {
  if (window.__pulseBgInited) return;
  window.__pulseBgInited = true;

  const THREE_CDN = 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js';

  function loadThree(cb) {
    if (window.THREE) return cb();
    const s = document.createElement('script');
    s.src = THREE_CDN;
    s.async = true;
    s.onload = cb;
    s.onerror = () => console.warn('PULSE bg: failed to load three.js');
    document.head.appendChild(s);
  }

  function mountWhenReady(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  function init() {
    const mount = document.getElementById('bg');
    if (!mount) return;

    const THREE = window.THREE;
    const w = () => mount.clientWidth || window.innerWidth;
    const h = () => mount.clientHeight || window.innerHeight;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(75, w() / h(), 0.1, 1000);
    camera.position.z = 3;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(w(), h());
    // Cap pixel ratio at 1.5 to keep performance stable on retina screens.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    // Lower subdivisions than the reference (was 64). 32 ~= 10k triangles
    // — visually identical for a wireframe and far cheaper.
    const geometry = new THREE.IcosahedronGeometry(1.2, 32);

    const material = new THREE.ShaderMaterial({
      uniforms: {
        time: { value: 0 },
        pointLightPosition: { value: new THREE.Vector3(0, 0, 5) },
        // White wireframe to match the AnomalousMatter reference look.
        color: { value: new THREE.Color(0xffffff) },
      },
      vertexShader: `
        uniform float time;
        varying vec3 vNormal;
        varying vec3 vPosition;

        vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
        vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
        vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
        vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
        float snoise(vec3 v){
          const vec2 C=vec2(1.0/6.0,1.0/3.0);
          const vec4 D=vec4(0.0,0.5,1.0,2.0);
          vec3 i=floor(v+dot(v,C.yyy));
          vec3 x0=v-i+dot(i,C.xxx);
          vec3 g=step(x0.yzx,x0.xyz);
          vec3 l=1.0-g;
          vec3 i1=min(g.xyz,l.zxy);
          vec3 i2=max(g.xyz,l.zxy);
          vec3 x1=x0-i1+C.xxx;
          vec3 x2=x0-i2+C.yyy;
          vec3 x3=x0-D.yyy;
          i=mod289(i);
          vec4 p=permute(permute(permute(
              i.z+vec4(0.0,i1.z,i2.z,1.0))
              +i.y+vec4(0.0,i1.y,i2.y,1.0))
              +i.x+vec4(0.0,i1.x,i2.x,1.0));
          float n_=0.142857142857;
          vec3 ns=n_*D.wyz-D.xzx;
          vec4 j=p-49.0*floor(p*ns.z*ns.z);
          vec4 x_=floor(j*ns.z);
          vec4 y_=floor(j-7.0*x_);
          vec4 x=x_*ns.x+ns.yyyy;
          vec4 y=y_*ns.x+ns.yyyy;
          vec4 h=1.0-abs(x)-abs(y);
          vec4 b0=vec4(x.xy,y.xy);
          vec4 b1=vec4(x.zw,y.zw);
          vec4 s0=floor(b0)*2.0+1.0;
          vec4 s1=floor(b1)*2.0+1.0;
          vec4 sh=-step(h,vec4(0.0));
          vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
          vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
          vec3 p0=vec3(a0.xy,h.x);
          vec3 p1=vec3(a0.zw,h.y);
          vec3 p2=vec3(a1.xy,h.z);
          vec3 p3=vec3(a1.zw,h.w);
          vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
          p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
          vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
          m=m*m;
          return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
        }

        void main() {
          vNormal = normal;
          vPosition = position;
          float d = snoise(position * 2.0 + time * 0.5) * 0.2;
          vec3 newPosition = position + normal * d;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(newPosition, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 color;
        uniform vec3 pointLightPosition;
        varying vec3 vNormal;
        varying vec3 vPosition;

        void main() {
          vec3 n = normalize(vNormal);
          vec3 lightDir = normalize(pointLightPosition - vPosition);
          float diffuse = max(dot(n, lightDir), 0.0);
          float fresnel = 1.0 - dot(n, vec3(0.0, 0.0, 1.0));
          fresnel = pow(fresnel, 2.0);
          vec3 finalColor = color * diffuse + color * fresnel * 0.5;
          gl_FragColor = vec4(finalColor, 1.0);
        }
      `,
      wireframe: true,
    });

    const mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);

    let frameId = null;
    let running = true;

    function animate(t) {
      if (!running) return;
      material.uniforms.time.value = t * 0.0003;
      mesh.rotation.y += 0.0005;
      mesh.rotation.x += 0.0002;
      renderer.render(scene, camera);
      frameId = requestAnimationFrame(animate);
    }
    animate(0);

    // Pause when the tab is hidden to save battery and CPU.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        running = false;
        if (frameId) cancelAnimationFrame(frameId);
      } else if (!running) {
        running = true;
        animate(performance.now());
      }
    });

    function onResize() {
      camera.aspect = w() / h();
      camera.updateProjectionMatrix();
      renderer.setSize(w(), h());
    }
    window.addEventListener('resize', onResize);

    function onMouseMove(e) {
      const x = (e.clientX / window.innerWidth) * 2 - 1;
      const y = -(e.clientY / window.innerHeight) * 2 + 1;
      const vec = new THREE.Vector3(x, y, 0.5).unproject(camera);
      const dir = vec.sub(camera.position).normalize();
      const dist = -camera.position.z / dir.z;
      const pos = camera.position.clone().add(dir.multiplyScalar(dist));
      material.uniforms.pointLightPosition.value.copy(pos);
    }
    // Throttle pointer updates — we don't need 240 Hz on a background.
    let lastMouse = 0;
    window.addEventListener('mousemove', (e) => {
      const now = performance.now();
      if (now - lastMouse < 40) return;
      lastMouse = now;
      onMouseMove(e);
    });
  }

  window.initBackground = function () {
    loadThree(() => mountWhenReady(init));
  };
})();

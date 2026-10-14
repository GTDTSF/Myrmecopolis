// 蚂蚁精灵模块：侧视剖面轮廓，加载时烘焙 8 方向
// 朝向索引与 sim 的 DIRS 一致：0左 1上 2右 3下 4左上 5右上 6左下 7右下
const ADIRS=[[-1,0],[0,-1],[1,0],[0,1],[-1,-1],[1,-1],[-1,1],[1,1]];
const ADIR_NAMES=["左","上","右","下","左上","右上","左下","右下"];

function mkCanvas(w,h){const c=document.createElement("canvas");c.width=w;c.height=h;return c;}

// rects: [x, y, w, h, color]
function stamp(c,rects){
  const g=c.getContext("2d");
  for(const r of rects){g.fillStyle=r[4];g.fillRect(r[0],r[1],r[2],r[3]);}
  return c;
}

const PAL={
  M:"#4a3420", ML:"#5f4529",  // 中躯（胸）
  H:"#4f3520", HL:"#63452b",  // 头
  P:"#3a2a18",                // 腹柄（细腰）
  E:"#1a120a",                // 复眼
  MD:"#c8ae7e", MD2:"#8f7752",// 上颚（近/远）
  A:"#33261a", A2:"#241a0f",  // 触角（近/远）
  LN:"#6b4a2a", LF:"#382718", // 腿（近/远）
  CL:"#8a6a44",               // 爪
};
function gasterPal(colony){
  return colony===0
    ? {G:"#d97b1c", GL:"#f0a040", GD:"#a35610"}  // 橙群：橙色腹（常见真实配色）
    : {G:"#3a7fc1", GL:"#63a3dd", GD:"#26578c"}; // 蓝群：腹部沿用阵营色
}

// 工蚁 30x14（朝右）：腹(9) 细腰(2) 中躯(8) 头(7) 上颚(3)
function workerRects(colony){
  const {G,GL,GD}=gasterPal(colony), p=PAL;
  return [
    // 腹 gaster（最大段，圆润，带节纹与高光）
    [2,3,7,1,GL],[1,4,9,1,G],[0,5,10,4,G],[1,9,9,1,G],[2,10,7,1,GD],
    [4,5,1,5,GD],[7,5,1,5,GD],
    // 腹柄 petiole（细腰，标志性）
    [10,5,2,4,p.P],
    // 中躯 mesosoma（生 6 足）
    [13,3,3,1,p.ML],[12,4,8,1,p.ML],[12,5,8,4,p.M],
    // 头 head
    [21,2,4,1,p.HL],[20,3,6,1,p.H],[20,4,7,5,p.H],[21,9,5,1,p.H],
    // 复眼
    [23,4,2,3,p.E],
    // 上颚（浅色几丁质，带齿）
    [27,5,2,1,p.MD],[28,6,2,1,p.MD],[27,7,2,1,p.MD2],
    // 膝状触角：近侧柄节→鞭节折弯，远侧短一截
    [25,2,1,1,p.A],[26,1,1,1,p.A],[27,1,1,1,p.A],[28,2,1,1,p.A],[29,3,1,1,p.A],
    [24,1,1,1,p.A2],[25,0,1,1,p.A2],[26,0,1,1,p.A2],
    // 近侧 3 腿（前/中/后）+ 爪
    [18,9,1,2,p.LN],[19,11,1,2,p.LN],[20,12,1,1,p.CL],
    [15,9,1,3,p.LN],[15,12,1,1,p.CL],
    [12,9,1,2,p.LN],[11,11,1,2,p.LN],[10,12,1,1,p.CL],
    // 远侧 3 腿（更短更暗，深度）
    [17,9,1,2,p.LF],[14,9,1,2,p.LF],[11,9,1,2,p.LF],
  ];
}
// 携带态：上颚夹食物
function workerCarryRects(colony){
  return workerRects(colony).concat([
    [27,4,3,3,"#a4c93c"],
    [27,4,3,1,"#c3e06a"],
  ]);
}

// 兵蚁 36x16（朝右）：头宽大色浅、巨大镰刀状上颚、深橙足
function soldierRects(colony){
  const {G,GL,GD}=gasterPal(colony), p=PAL;
  const LG="#a05a18";
  return [
    // 腹（更大）
    [2,3,8,1,GL],[1,4,10,1,G],[0,5,12,6,G],[1,11,10,1,G],[2,12,8,1,GD],
    [5,5,1,7,GD],[9,5,1,7,GD],
    // 细腰
    [12,6,2,5,p.P],
    // 中躯（更大）
    [15,3,4,1,p.ML],[14,4,9,1,p.ML],[14,5,9,5,p.M],
    // 头（宽、色浅 = 兵蚁特征）
    [25,2,5,1,"#9c7b50"],[24,3,7,1,"#8a6a45"],[24,4,8,7,"#7d5f3c"],[25,11,6,1,"#6b5030"],
    // 复眼
    [28,5,2,3,p.E],
    // 巨大镰刀状上颚（象牙色，前下弯）
    [31,5,3,1,p.MD],[33,6,2,1,p.MD],[34,7,1,2,p.MD],[32,6,1,1,p.MD2],
    // 膝状触角
    [27,2,1,1,p.A],[28,1,1,1,p.A],[29,1,1,1,p.A],[30,2,1,1,p.A],[31,3,1,1,p.A],
    [26,1,1,1,p.A2],[27,0,1,1,p.A2],[28,0,1,1,p.A2],
    // 近侧 3 腿（深橙 = 兵蚁特征）+ 爪
    [20,10,1,2,LG],[21,12,1,2,LG],[22,13,1,1,p.CL],
    [17,10,1,3,LG],[17,13,1,1,p.CL],
    [14,10,1,2,LG],[13,12,1,2,LG],[12,13,1,1,p.CL],
    // 远侧 3 腿
    [19,10,1,2,p.LF],[16,10,1,2,p.LF],[13,10,1,2,p.LF],
  ];
}

// 蚁后 38x16（朝右，不动）：巨大腹部、小头短腿
function queenRects(colony){
  const {G,GL,GD}=gasterPal(colony), p=PAL;
  return [
    // 巨腹
    [3,2,11,1,GL],[2,3,13,1,G],[1,4,15,10,G],[2,14,13,1,G],[3,15,11,1,GD],
    [6,4,1,10,GD],[11,4,1,10,GD],
    // 细腰
    [18,8,3,4,p.P],
    // 中躯
    [22,5,3,1,p.ML],[21,6,8,1,p.ML],[21,7,8,4,p.M],
    // 头（小）
    [29,5,3,1,p.HL],[28,6,5,1,p.H],[28,7,6,5,p.H],[29,12,4,1,p.H],
    // 复眼
    [31,7,2,3,p.E],
    // 小上颚
    [34,8,2,1,p.MD],[34,10,2,1,p.MD2],
    // 短触角
    [30,4,1,1,p.A],[31,3,1,1,p.A],[32,4,1,1,p.A],
    [29,3,1,1,p.A2],[30,2,1,1,p.A2],
    // 短腿（蚁后基本不动）
    [25,11,1,2,p.LN],[24,13,1,1,p.CL],
    [22,11,1,2,p.LN],[21,13,1,1,p.CL],
    [28,11,1,2,p.LN],[29,13,1,1,p.CL],
    [26,11,1,1,p.LF],[23,11,1,1,p.LF],
  ];
}

// 8 方向对应角度（canvas y 轴向下）：ADIRS 顺序
const AANG=[Math.PI,-Math.PI/2,0,Math.PI/2,-Math.PI*3/4,-Math.PI/4,Math.PI*3/4,Math.PI/4];

let SPRITES=null;
function initAnts(){
  if(SPRITES)return;
  SPRITES={worker:[],soldier:[],queen:[]};
  for(const col of [0,1]){
    // 只烘焙朝右基础精灵，转向用渲染时旋转变换
    SPRITES.worker[col]={
      move:stamp(mkCanvas(30,14),workerRects(col)),
      carry:stamp(mkCanvas(30,14),workerCarryRects(col)),
    };
    SPRITES.soldier[col]={
      move:stamp(mkCanvas(36,16),soldierRects(col)),
    };
    SPRITES.queen[col]=stamp(mkCanvas(38,17),queenRects(col));
  }
}
// 蚂蚁渲染缩放：1.0=原生像素（不缩放，保细节）；改"蚂蚁大小比例"靠扩大地图 S（见 index.html），不缩精灵
const ANT_SCALE=1.0;
// 以 (px,py) 为中心、按 angle（弧度）旋转绘制
function drawAnt(ctx,px,py,caste,colony,carrying,angle,sz=1){
  const base=caste==="soldier"?SPRITES.soldier[colony].move:SPRITES.worker[colony][carrying?"carry":"move"];
  ctx.save();
  ctx.translate(px,py);
  ctx.scale(ANT_SCALE*sz,ANT_SCALE*sz);
  ctx.rotate(angle);
  ctx.drawImage(base,-base.width/2,-base.height/2);
  ctx.restore();
}
function drawQueen(ctx,px,py,colony){
  const s=SPRITES.queen[colony];
  ctx.save();
  ctx.translate(px,py);
  ctx.scale(ANT_SCALE,ANT_SCALE);
  ctx.drawImage(s,-s.width/2,-s.height/2);
  ctx.restore();
}

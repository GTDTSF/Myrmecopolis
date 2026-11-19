// 蚂蚁精灵模块：侧视剖面轮廓，按部位分级建模，加载时烘焙
// 朝向索引与 sim 的 DIRS 一致：0左 1上 2右 3下 4左上 5右上 6左下 7右下
const ADIRS=[[-1,0],[0,-1],[1,0],[0,1],[-1,-1],[1,-1],[-1,1],[1,1]];
const ADIR_NAMES=["左","上","右","下","左上","右上","左下","右下"];

function mkCanvas(w,h){const c=document.createElement("canvas");c.width=w;c.height=h;return c;}

// rect: [x, y, w, h, color]
function stamp(c,rects){
  const g=c.getContext("2d");
  for(const r of rects){g.fillStyle=r[4];g.fillRect(r[0],r[1],r[2],r[3]);}
  return c;
}

// 部位调色板：各部位独立取色——细腰最深（标志性缢缩）、上颚浅几丁质（口器）、头比中躯偏浅
const PAL={
  M:"#54391f", ML:"#6a4a29",        // 中躯（胸）
  H:"#634123", HL:"#7a5230",        // 头
  P:"#241608",                      // 细腰/缢缩（最深）
  E:"#120c06", EH:"#8a6a3a",        // 复眼 + 高光
  MD:"#d8c08a", MD2:"#9a7f55",      // 上颚（近/远，浅几丁质）
  A:"#33261a", A2:"#241a0f",        // 触角（近/远）
  LN:"#6b4a2a", LF:"#382718",       // 腿（近/远）
  CL:"#8a6a44",                     // 爪
};
// 三类工蚁靠"腹部颜色"区分（阶段21）：0=觅食 橙 / 1=建造 蓝 / 2=维护 绿
const ROLE_COLORS=[
  {G:"#d97b1c", GL:"#f0a040", GD:"#a35610"},
  {G:"#3a7fc1", GL:"#63a3dd", GD:"#26578c"},
  {G:"#2fa06a", GL:"#55c98f", GD:"#1d7048"},
];
function gasterPal(role){
  return ROLE_COLORS[role|0] || ROLE_COLORS[0];
}
// 部位 = 命名矩形组；身体 = 有序部位表（后列部位叠绘在前列之上）
// 部位顺序（后→前）：腹 → 细腰 → 中躯 → 头 → 眼 → 上颚 → 触角 → 腿
function flat(parts){return Object.values(parts).flat();}
// 部位中心相对画布中心的偏移（精灵局部单位）——外部装饰（叼粮粮粒等）锚定用，几何改了自动跟随
function partOffset(rects,cw,ch){
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const r of rects){x0=Math.min(x0,r[0]);y0=Math.min(y0,r[1]);x1=Math.max(x1,r[0]+r[2]);y1=Math.max(y1,r[1]+r[3]);}
  return [(x0+x1)/2-cw/2,(y0+y1)/2-ch/2];
}
// 嘴锚点 = 上颚部位中心（叼粮粮粒画在这里，随蚂蚁朝向旋转；见 index.html drawCarryFood）
const MOUTH_OFFSET=partOffset(workerParts(0).mandible,30,14);

// 工蚁 30x14（朝右）：部位逐一分离——腹(10px)→细腰(3px 缢缩)→中躯(7px)→[1px 颈缝]→头(7px)
// 上颚为头前伸出的上下两条浅几丁质钳（夹口）；叼粮粮粒不在精灵内（屏幕层画粮仓同款圆粒，锚定 MOUTH_OFFSET）
function workerParts(role){
  const {G,GL,GD}=gasterPal(role), p=PAL;
  return {
    gaster:  [[3,2,6,1,GL],[2,3,8,1,G],[1,4,9,1,G],[0,5,10,4,G],[1,9,9,1,G],[2,10,8,1,GD],[3,11,6,1,GD],[4,4,1,7,GD],[7,4,1,7,GD]],
    petiole: [[10,5,3,4,p.P]],
    meso:    [[14,3,5,1,p.ML],[13,4,7,5,p.M],[14,9,5,1,p.M]],
    head:    [[22,2,5,1,p.HL],[21,3,6,1,p.H],[21,4,7,5,p.H],[22,9,5,1,p.H]],
    eye:     [[23,4,3,3,p.E],[25,4,1,1,p.EH]],
    mandible:[[26,4,4,1,p.MD],[29,5,1,1,p.MD],[26,9,4,1,p.MD2],[29,8,1,1,p.MD2]],
    antenna: [[25,2,1,1,p.A],[26,1,1,1,p.A],[27,1,1,1,p.A],[28,2,1,1,p.A],[29,3,1,1,p.A],[23,1,1,1,p.A2],[24,0,1,1,p.A2],[25,0,1,1,p.A2]],
    legs:    [[18,9,1,2,p.LN],[19,11,1,2,p.LN],[20,12,1,1,p.CL],[15,9,1,3,p.LN],[15,12,1,1,p.CL],[12,9,1,2,p.LN],[11,11,1,2,p.LN],[10,12,1,1,p.CL],[17,9,1,2,p.LF],[14,9,1,2,p.LF],[11,9,1,2,p.LF]],
  };
}
// 携带态：精灵本体与移动态相同；粮粒=世界粮（粮仓同款颗粒），由 index.html 画在嘴锚点上
function workerCarryParts(role){return workerParts(role);}
function workerRects(role){return flat(workerParts(role));}
function workerCarryRects(role){return workerRects(role);}

// 兵蚁 36x16（朝右）：头宽大色浅、巨大镰刀状上颚（闭合钳口）、深橙足
function soldierParts(colony){
  const {G,GL,GD}=gasterPal(0), p=PAL, LG="#a05a18";
  return {
    gaster:  [[4,2,7,1,GL],[2,3,9,1,G],[1,4,11,1,G],[0,5,12,6,G],[1,11,11,1,G],[2,12,10,1,GD],[3,13,8,1,GD],[5,4,1,8,GD],[9,4,1,8,GD]],
    petiole: [[12,5,3,5,p.P]],
    meso:    [[17,3,6,1,p.ML],[16,4,8,1,p.ML],[16,5,8,5,p.M],[17,10,6,1,p.M]],
    head:    [[27,2,5,1,"#9c7b50"],[26,3,7,1,"#8a6a45"],[25,4,8,6,"#7d5f3c"],[26,10,6,1,"#6b5030"]],
    eye:     [[29,5,3,3,p.E],[31,5,1,1,p.EH]],
    mandible:[[33,4,2,1,p.MD],[35,5,1,3,p.MD],[33,11,2,1,p.MD2],[35,8,1,3,p.MD2]],
    antenna: [[29,2,1,1,p.A],[30,1,1,1,p.A],[31,1,1,1,p.A],[32,2,1,1,p.A],[33,3,1,1,p.A],[27,1,1,1,p.A2],[28,0,1,1,p.A2],[29,0,1,1,p.A2]],
    legs:    [[22,10,1,3,LG],[23,13,1,2,LG],[24,15,1,1,p.CL],[19,10,1,4,LG],[19,14,1,1,p.CL],[16,10,1,3,LG],[15,13,1,2,LG],[14,15,1,1,p.CL],[21,10,1,3,p.LF],[18,10,1,3,p.LF],[15,10,1,3,p.LF]],
  };
}
function soldierRects(colony){return flat(soldierParts(colony));}

// 蚁后 38x17（朝右，不动）：巨大腹部、小头短腿
function queenParts(){
  const {G,GL,GD}=gasterPal(0), p=PAL;
  return {
    gaster:  [[3,2,11,1,GL],[2,3,13,1,G],[1,4,15,10,G],[2,14,13,1,G],[3,15,11,1,GD],[6,4,1,10,GD],[11,4,1,10,GD]],
    petiole: [[18,8,3,4,p.P]],
    meso:    [[22,5,3,1,p.ML],[21,6,8,1,p.ML],[21,7,8,4,p.M]],
    head:    [[29,5,3,1,p.HL],[28,6,5,1,p.H],[28,7,6,5,p.H],[29,12,4,1,p.H]],
    eye:     [[31,7,2,3,p.E]],
    mandible:[[34,8,2,1,p.MD],[34,10,2,1,p.MD2]],
    antenna: [[30,4,1,1,p.A],[31,3,1,1,p.A],[32,4,1,1,p.A],[29,3,1,1,p.A2],[30,2,1,1,p.A2]],
    legs:    [[25,11,1,2,p.LN],[24,13,1,1,p.CL],[22,11,1,2,p.LN],[21,13,1,1,p.CL],[28,11,1,2,p.LN],[29,13,1,1,p.CL],[26,11,1,1,p.LF],[23,11,1,1,p.LF]],
  };
}
function queenRects(){return flat(queenParts());}

// 8 方向对应角度（canvas y 轴向下）：ADIRS 顺序
const AANG=[Math.PI,-Math.PI/2,0,Math.PI/2,-Math.PI*3/4,-Math.PI/4,Math.PI*3/4,Math.PI/4];

let SPRITES=null;
function initAnts(){
  if(SPRITES)return;
  SPRITES={worker:[],soldier:[],queen:[]};
  for(const col of [0,1]){
    // 只烘焙朝右基础精灵，转向用渲染时旋转变换
    SPRITES.worker[col]=[0,1,2].map(role=>({
      move:stamp(mkCanvas(30,14),workerRects(role)),
      carry:stamp(mkCanvas(30,14),workerCarryRects(role)),
    }));
    SPRITES.soldier[col]={
      move:stamp(mkCanvas(36,16),soldierRects(col)),
    };
    SPRITES.queen[col]=stamp(mkCanvas(38,17),queenRects());
  }
}
// 蚂蚁渲染缩放：1.0=原生像素（不缩放，保细节）；改"蚂蚁大小比例"靠扩大地图 S（见 index.html），不缩精灵
const ANT_SCALE=1.0;
// 以 (px,py) 为中心、按 angle（弧度）旋转绘制
function drawAnt(ctx,px,py,caste,colony,carrying,angle,sz=1,role=0){
  const base=caste==="soldier"?SPRITES.soldier[colony].move:SPRITES.worker[colony][role|0][carrying?"carry":"move"];
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

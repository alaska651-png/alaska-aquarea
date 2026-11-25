// HA session only. The iframe gets a one-use 15-second grant, never an HA bearer token.
class AquareaClimaHost extends HTMLElement {
  setConfig(config){
    if(this.frame)return;
    this.attachShadow({mode:'open'});
    this.shadowRoot.innerHTML='<style>:host{display:block;position:fixed;inset:0;z-index:1000;background:#001016;height:100dvh}iframe{width:100%;height:100%;border:0;display:block}:host(.maintenance){position:relative;inset:auto;z-index:auto;height:calc(100vh - 90px)}.status{position:absolute;inset:0;display:grid;place-items:center;color:#d7f5fa;background:#001016;font:16px system-ui}.status[hidden]{display:none}</style>';
    this.status=document.createElement('div');this.status.className='status';this.status.textContent='Connessione a Home Assistant…';
    this.frame=document.createElement('iframe');this.frame.title='Aquarea Clima';this.frame.allow='fullscreen';this.frame.setAttribute('allowfullscreen','');this.shadowRoot.append(this.frame,this.status);
    this.listener=event=>{if(event.source!==this.frame.contentWindow||event.origin!==location.origin)return;if(event.data?.type==='aquarea-clima-exit')this.classList.toggle('maintenance');if(event.data?.type==='aquarea-auth-required')this.renew(true);};
    this.visible=()=>{if(!document.hidden)this.renew(true);};
  }
  set hass(value){this._hass=value;this.renew(false);}
  async renew(force){
    if(!this._hass||!this.frame||!this.isConnected||this.busy||(!force&&Date.now()-(this.last||0)<55000))return;
    this.busy=true;
    try{
      const grant=await this._hass.callWS({type:'aquarea_clima/session',origin:location.origin});
      if(force||this.tag!==grant.tag){this.frame.src=new URL(grant.url,location.origin).href;this.tag=grant.tag;}
      this.last=Date.now();this.status.hidden=true;
    }catch{this.status.hidden=false;this.status.textContent='Collegamento al sinottico non disponibile. Verifica la sessione Home Assistant.';}
    finally{this.busy=false;}
  }
  connectedCallback(){if(this.listener)window.addEventListener('message',this.listener);if(this.visible)document.addEventListener('visibilitychange',this.visible);this.timer=setInterval(()=>this.renew(false),60000);this.renew(true);}
  disconnectedCallback(){window.removeEventListener('message',this.listener);document.removeEventListener('visibilitychange',this.visible);clearInterval(this.timer);}
  getCardSize(){return 20;}
}
if(!customElements.get('aquarea-clima-host'))customElements.define('aquarea-clima-host',AquareaClimaHost);

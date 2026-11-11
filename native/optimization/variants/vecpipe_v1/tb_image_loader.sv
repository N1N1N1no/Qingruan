`timescale 1ns/1ps
// Qualification of canonical byte-addressed RAM files, not CPU inference.
module tb_image_loader;
localparam RAM_BYTES=67108864,MAX_BINARY_BYTES=30861636;
reg[7:0]memory[0:RAM_BYTES-1];
byte unsigned expected[0:MAX_BINARY_BYTES-1];
string manifest_path,image_name,hex_path,binary_path;
integer fd,source_fd,rc,images=0,sections,nbytes,loaded,i,j,extra;
reg[31:0]address;
longint unsigned offset,checked_bytes=0;
initial begin
 if(!$value$plusargs("MANIFEST=%s",manifest_path))$fatal(1,"IMAGE_LOADER_FAIL manifest argument");
 fd=$fopen(manifest_path,"r");if(!fd)$fatal(1,"IMAGE_LOADER_FAIL manifest open");
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%s %s %d\n",image_name,hex_path,sections);
  if(rc!=3||sections!=8)$fatal(1,"IMAGE_LOADER_FAIL image header");
  $readmemh(hex_path,memory);
  for(j=0;j<sections;j=j+1)begin
   rc=$fscanf(fd,"%h %d %s\n",address,nbytes,binary_path);
   offset={32'b0,address}-64'h80000000;
   if(rc!=3||nbytes<1||nbytes>MAX_BINARY_BYTES||address<32'h80000000||offset+nbytes>RAM_BYTES)
    $fatal(1,"IMAGE_LOADER_FAIL section bounds");
   source_fd=$fopen(binary_path,"rb");if(!source_fd)$fatal(1,"IMAGE_LOADER_FAIL source open");
   loaded=$fread(expected,source_fd,0,nbytes);extra=$fgetc(source_fd);$fclose(source_fd);
   if(loaded!=nbytes||extra!=-1)$fatal(1,"IMAGE_LOADER_FAIL source length");
   for(i=0;i<nbytes;i=i+1)begin
    if(memory[offset+i]!==expected[i])$fatal(1,"IMAGE_LOADER_FAIL image=%s section=%0d offset=%0d got=%02x expected=%02x",image_name,j,i,memory[offset+i],expected[i]);
   end
   checked_bytes=checked_bytes+nbytes;
  end
  images=images+1;
  $display("IMAGE_LOADER_CASE image=%s sections=%0d cumulative_checked_bytes=%0d",image_name,sections,checked_bytes);
 end
 $fclose(fd);
 if(images!=20||checked_bytes!=64'd730528720)$fatal(1,"IMAGE_LOADER_FAIL incomplete coverage images=%0d bytes=%0d",images,checked_bytes);
 $display("ALL20_CANONICAL_RAM_VCS_LOADER_PASS images=20 sections=160 checked_bytes=%0d",checked_bytes);
 $finish;
end
endmodule
